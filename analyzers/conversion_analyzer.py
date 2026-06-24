"""Business analysis layer for conversion job (no Excel / Telegram)."""

from __future__ import annotations

import os
import re
from typing import Callable

import pandas as pd

from analyzers.conversion_dto import ConversionAnalysisResult, SpecialCardsState
from core.rules_v2.accessors import ConversionRulesAccessor
from core.rules_v2.models import RulesSnapshotV2
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from utils.normalization import parse_dt_series_msk

icon, name = LOG_PROFILES["CONVERT"]
logger = get_logger(name, icon)


def normalize_colname(name: str) -> str:
    return str(name).strip().lower().replace("ё", "е")


def normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    name = name.lower().strip()
    name = name.replace("ё", "е")
    name = name.replace("амобайл", "а-мобайл")
    name = re.sub(r"\(\d+\)$", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip(", ")


def normalize_partners_list(partners_str: str) -> list:
    partners = str(partners_str).split(",")
    return [normalize_name(p) for p in partners if p.strip()]


def load_data(filepath, col_mapping: dict):
    """
    Универсальная загрузка CSV/XLSX и нормализация колонок по mappingу вида:
    {"card": "Карта", "partner": "Партнёр", "status": "Статус", "datetime": "Дата/Время создания", ...}
    """
    if filepath.endswith((".xlsx", ".xls")):
        df = pd.read_excel(filepath, dtype=str)
    else:
        df = pd.read_csv(filepath, sep=None, engine="python", encoding="utf-8", dtype=str)

    logger.info(f"[load_data] Загружен файл {filepath} с колонками: {list(df.columns)}")

    norm_cols = {normalize_colname(c): c for c in df.columns}
    new_cols = {}
    for key, expected_name in col_mapping.items():
        expected_norm = normalize_colname(expected_name)
        if expected_norm not in norm_cols:
            raise ValueError(
                f"❌ В файле {os.path.basename(filepath)} нет колонки '{expected_name}' "
                f"(ожидали для '{key}')"
            )
        new_cols[norm_cols[expected_norm]] = key

    df.rename(columns=new_cols, inplace=True)

    for c in ["card", "status"]:
        if c in df:
            df[c] = df[c].astype(str).str.strip().str.lower()

    if "partner" in df:
        df["original_partner"] = df["partner"].astype(str).str.strip()
        df["partner"] = df["partner"].astype(str).str.strip().str.lower()

    if "datetime" in df:
        raw_datetime = df["datetime"].copy()
        df["datetime"] = parse_dt_series_msk(df["datetime"])

        invalid_datetime_mask = raw_datetime.notna() & df["datetime"].isna()
        if invalid_datetime_mask.any():
            bad_examples = raw_datetime[invalid_datetime_mask].astype(str).unique()[:10]
            logger.warning(
                f"[load_data] Некорректный формат datetime. "
                f"Ожидается ДД.ММ.ГГГГ ЧЧ:ММ:СС. "
                f"Невалидных строк: {int(invalid_datetime_mask.sum())}. "
                f"Примеры: {', '.join(bad_examples)}"
            )

    required = [c for c in ["card", "status", "datetime"] if c in df.columns]
    if required:
        df.dropna(subset=required, inplace=True)

    if "datetime" in df:
        df.sort_values("datetime", ascending=False, inplace=True)

    return df


def count_last_error_streak(df: pd.DataFrame) -> pd.DataFrame:
    """
    Возвращает DataFrame с длиной последней непрерывной серии ошибок для каждой пары (карта, партнёр).
    Берём записи по убыванию даты, считаем подряд идущие 'ошибка' до первого 'оплачен'
    (или иного статуса ≠ 'ошибка').
    """
    results = []
    if df.empty:
        return pd.DataFrame(columns=["card", "partner_norm", "max_consecutive_errors"])

    for (card, partner), group in df.groupby(["card", "partner_norm"], sort=False):
        statuses = group.sort_values("datetime", ascending=False)["status"].tolist()
        streak = 0
        for s in statuses:
            if s == "ошибка":
                streak += 1
            elif s == "оплачен":
                break
            else:
                break
        results.append({"card": card, "partner_norm": partner, "max_consecutive_errors": streak})
    return pd.DataFrame(results)


def _latest_original_partners(conv_df: pd.DataFrame) -> pd.DataFrame:
    if conv_df.empty or "original_partner" not in conv_df.columns:
        return pd.DataFrame(columns=["card", "partner_norm", "original_partner"])

    return (
        conv_df.sort_values("datetime", ascending=False)
        .drop_duplicates(subset=["card", "partner_norm"], keep="first")[["card", "partner_norm", "original_partner"]]
    )


def _order_problem_columns(problem: pd.DataFrame) -> pd.DataFrame:
    preferred = [
        "card",
        "partner",
        "original_partner",
        "max_consecutive_errors",
        "threshold",
        "status",
        "partner_list",
    ]
    ordered = [c for c in preferred if c in problem.columns]
    rest = [c for c in problem.columns if c not in ordered]
    return problem[ordered + rest]


class ConversionAnalyzer:
    """Pure business analysis for conversion job."""

    def load_special_cards_state(
        self,
        *,
        dropbox_special_file: str,
        local_special_path: str,
        download_fn: Callable[[str, str], bool],
    ) -> SpecialCardsState:
        special_rules: dict = {}
        latest_special_date = None
        special_loaded = False
        df_special = None

        if download_fn(dropbox_special_file, local_special_path):
            logger.info(
                f"[analyze] 📥 special_cards.xlsx загружен: {dropbox_special_file} → {local_special_path}"
            )
            df_special = pd.read_excel(local_special_path, dtype=str)

            df_special.rename(
                columns={
                    "Карта": "card",
                    "Партнер": "partner",
                    "Дата": "start_date",
                },
                inplace=True,
            )

            missing_cols = [c for c in ["card", "partner", "start_date"] if c not in df_special.columns]
            if missing_cols:
                logger.warning(f"[analyze] ⚠️ В special_cards.xlsx нет колонок: {missing_cols}")
            else:
                df_special["card"] = df_special["card"].astype(str).str.strip()
                df_special["partner_norm"] = df_special["partner"].apply(normalize_name)
                raw_start_date = df_special["start_date"].copy()
                df_special["start_date"] = pd.to_datetime(
                    df_special["start_date"],
                    format="%d.%m.%Y",
                    errors="coerce",
                ).dt.date

                invalid_start_date_mask = raw_start_date.notna() & df_special["start_date"].isna()
                if invalid_start_date_mask.any():
                    bad_examples = raw_start_date[invalid_start_date_mask].astype(str).unique()[:10]
                    logger.warning(
                        f"[analyze] В special_cards.xlsx есть некорректные start_date. "
                        f"Ожидается ДД.ММ.ГГГГ ЧЧ:ММ:СС. "
                        f"Невалидных строк: {int(invalid_start_date_mask.sum())}. "
                        f"Примеры: {', '.join(bad_examples)}"
                    )
                df_special.sort_values("start_date", ascending=False, inplace=True, na_position="last")
                df_special.drop_duplicates(subset=["card", "partner_norm"], keep="first", inplace=True)

                special_rules = df_special.set_index(["card", "partner_norm"])["start_date"].to_dict()
                latest_special_date = df_special["start_date"].max()
                special_loaded = True
        else:
            logger.warning("[analyze] ⚠️ Не удалось скачать special_cards.xlsx из Dropbox.")

        return SpecialCardsState(
            special_rules=special_rules,
            latest_special_date=latest_special_date,
            special_loaded=special_loaded,
            df_special=df_special,
        )

    def analyze(
        self,
        *,
        conv_file: str,
        card_files: list | None,
        col_mapping: dict,
        snapshot: RulesSnapshotV2,
        special_state: SpecialCardsState,
    ) -> ConversionAnalysisResult:
        logger.info(f"[analyze] 🚀 Начало анализа: {os.path.basename(conv_file)}")

        rules = ConversionRulesAccessor.from_snapshot(snapshot)
        valid_statuses = rules.get_valid_statuses()
        logger.info(f"[analyze] valid_statuses for conversion: {sorted(valid_statuses)}")
        special_rules = special_state.special_rules

        conv_df = load_data(conv_file, col_mapping)

        conv_df["status"] = conv_df["status"].astype(str).str.strip().str.lower()
        conv_df["partner_norm"] = conv_df["partner"].apply(normalize_name)

        conv_df.dropna(subset=["card", "datetime", "status"], inplace=True)

        allowed_statuses = {"ошибка", "оплачен"} | valid_statuses
        conv_df = conv_df[conv_df["status"].isin(allowed_statuses)]
        logger.info(f"[analyze] 📄 Загружено {len(conv_df)} строк из conversion.")

        if special_rules:
            before = len(conv_df)
            key_tuples = list(special_rules.keys())
            if key_tuples:
                rules_df = pd.DataFrame(
                    [(card, partner, dt) for (card, partner), dt in special_rules.items()],
                    columns=["card", "partner_norm", "start_date"],
                )
                conv_df = conv_df.merge(rules_df, on=["card", "partner_norm"], how="left")

                conv_df["_op_date"] = conv_df["datetime"].dt.date
                mask_keep = (conv_df["start_date"].isna()) | (conv_df["_op_date"] >= conv_df["start_date"])
                conv_df = conv_df.loc[mask_keep].drop(columns=["start_date", "_op_date"])

            after = len(conv_df)
            if after != before:
                logger.info(f"[analyze] 🧭 Применены правила special_cards: отфильтровано {before - after} строк.")

        excluded_total = 0
        for rule in rules.get_partner_exclusion_rules():
            partner_norm = rules.resolve_exclusion_partner_norm(rule, normalize_name)
            if not partner_norm:
                continue

            before = len(conv_df)

            mask = (
                (conv_df["partner_norm"] == partner_norm)
                & (conv_df["datetime"].between(rule.start_dt, rule.end_dt))
            )
            conv_df = conv_df[~mask]

            excluded = before - len(conv_df)
            excluded_total += excluded
            if excluded > 0:
                logger.info(f"[analyze] ⏳ Исключено {excluded} строк по exclude для «{partner_norm}»")
        logger.info(f"[analyze] total excluded by rules exclude_time: {excluded_total}")

        card_df_list = []
        for f in card_files or []:
            try:
                card_df_list.append(
                    load_data(f, {"card": "Карта", "partner": "Партнёр", "status": "Статус", "pool": "Пул"})
                )
            except Exception as e:
                logger.warning(f"[analyze] ⚠️ Пропускаю card-файл {os.path.basename(f)}: {e}")

        card_df = pd.concat(card_df_list, ignore_index=True) if card_df_list else pd.DataFrame(
            columns=["card", "partner", "status"]
        )
        if not card_df.empty:
            card_df["partner_list"] = card_df["partner"].apply(normalize_partners_list)
            card_df["status"] = card_df["status"].astype(str).str.strip().str.lower()
        else:
            card_df["partner_list"] = []

        logger.info(f"[analyze] 🧩 Загружено {len(card_df)} карт из card-файлов.")
        logger.info(f"[analyze] Колонки в card_df: {list(card_df.columns)}")

        conv_df.sort_values(["card", "partner_norm", "datetime"], inplace=True)
        max_errors = count_last_error_streak(conv_df)

        threshold_map = rules.get_error_streak_threshold_map(normalize_name)
        logger.info(f"[analyze] thresholds loaded for conversion: {len(threshold_map)} partners")

        merged = max_errors.merge(_latest_original_partners(conv_df), on=["card", "partner_norm"], how="left")
        merged["threshold"] = merged["partner_norm"].map(threshold_map).fillna(rules.get_default_threshold()).astype(int)

        card_status_map = card_df.set_index("card")["status"].to_dict() if not card_df.empty else {}
        card_partners_map = card_df.set_index("card")["partner_list"].to_dict() if not card_df.empty else {}

        merged["status"] = merged["card"].map(card_status_map).astype(str).str.strip().str.lower()
        merged["partner_list"] = merged["card"].map(card_partners_map)

        problem_mask = (
            (merged["max_consecutive_errors"] >= merged["threshold"])
            & (merged["status"].isin(valid_statuses))
            & merged.apply(
                lambda r: isinstance(r["partner_list"], list) and r["partner_norm"] in r["partner_list"],
                axis=1,
            )
        )
        problem = merged.loc[problem_mask].copy()
        problem.rename(columns={"partner_norm": "partner"}, inplace=True)
        problem = _order_problem_columns(problem)

        active_statuses = list(valid_statuses)

        active_cards = card_df[
            card_df["status"].isin(active_statuses)
            & card_df["partner"].notna()
            & (card_df["partner"].str.strip() != "")
        ].copy()

        if not problem.empty:
            active_cards = active_cards[~active_cards["card"].isin(problem["card"])]

        def split_partners(row):
            parts = [p.strip() for p in str(row["partner"]).split(",") if p.strip()]
            return [(p, row["card"]) for p in parts]

        pairs = active_cards.apply(split_partners, axis=1).explode().dropna()
        pairs = pd.DataFrame(
            pairs.tolist(),
            columns=["partner_display", "card"],
        )

        cards_in_work_by_partner = (
            pairs.drop_duplicates(subset=["partner_display", "card"])
            .groupby("partner_display")["card"]
            .nunique()
            .sort_values(ascending=False)
        )

        if "pool" in card_df.columns:
            active_pools = card_df[
                card_df["status"].isin(active_statuses)
                & card_df["pool"].notna()
                & (card_df["pool"].str.strip() != "")
            ].copy()

            if not problem.empty:
                active_pools = active_pools[~active_pools["card"].isin(problem["card"])]

            cards_in_work_by_pool = (
                active_pools.drop_duplicates(subset=["pool", "card"])
                .groupby("pool")["card"]
                .nunique()
                .sort_values(ascending=False)
            )
        else:
            cards_in_work_by_pool = pd.Series(dtype=int)

        summary = {
            "Карт в работе по партнёрам": cards_in_work_by_partner.to_dict(),
            "Карт в работе по пулам": cards_in_work_by_pool.to_dict(),
            "Max ошибки": int(merged["max_consecutive_errors"].max()) if not merged.empty else 0,
            "Карты на отключение": int(problem["card"].nunique() if not problem.empty else 0),
        }

        logger.info(f"[analyze] ✅ Обнаружено {summary['Карты на отключение']} карт на отключение.")
        logger.info(f"[analyze] 📊 Карт в работе по партнёрам: {summary['Карт в работе по партнёрам']}")

        return ConversionAnalysisResult(
            summary=summary,
            problem_cards=problem,
            cards_in_work_by_partner=cards_in_work_by_partner,
            cards_in_work_by_pool=cards_in_work_by_pool,
            special_cards_state=special_state,
        )
