# analyzers/conversion.py

import os
import tempfile

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.telegram_bot import send_message_sync, send_file_sync
from integrations.dropbox_watcher import download_file
from integrations.conversion_wallet_editor_bridge import (
    maybe_enqueue_wallet_editor_from_problem_cards,
)
from core.datetime_utils import now_msk
from core.rules_provider import get_snapshot_v2

from analyzers.conversion_analyzer import (
    ConversionAnalyzer,
    count_last_error_streak,
    load_data,
    normalize_colname,
    normalize_name,
    normalize_partners_list,
)
from analyzers.conversion_dto import SpecialCardsState
from reporters.conversion_reporter import (
    render_excel,
    render_special_cards_latest_date_message,
    render_special_cards_load_failure,
    render_special_cards_loaded_message,
    render_telegram,
)


icon, name = LOG_PROFILES["CONVERT"]
logger = get_logger(name, icon)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID_ANALIZ") or "").strip()

_analyzer = ConversionAnalyzer()


def _send_telegram_messages(messages: tuple[str, ...]) -> None:
    for text in messages:
        send_message_sync(text, chat_id=CHAT_ID)


def _notify_special_cards_loaded(special_state: SpecialCardsState, *, send_telegram: bool) -> None:
    if not send_telegram:
        return

    message = render_special_cards_loaded_message(special_state, now_msk().date())
    if message is None:
        return

    send_message_sync(message, chat_id=CHAT_ID)
    if special_state.df_special is not None:
        today = now_msk().date()
        today_special = special_state.df_special[special_state.df_special["start_date"] == today]
        if not today_special.empty:
            logger.info(f"[run] 📊 Найдено {len(today_special)} новых special-карт.")


def _notify_special_cards_load_failure(*, send_telegram: bool, error: bool = False) -> None:
    if not send_telegram:
        return
    send_message_sync(render_special_cards_load_failure(error=error), chat_id=CHAT_ID)


def _notify_special_cards_latest_date(special_state: SpecialCardsState, *, send_telegram: bool) -> None:
    if not send_telegram:
        return
    message = render_special_cards_latest_date_message(special_state)
    if message is not None:
        send_message_sync(message, chat_id=CHAT_ID)


def run(
    conv_file: str,
    card_files: list,
    col_mapping: dict,
    *,
    generate_excel: bool = True,
    send_telegram: bool = True,
    rules_force_sync: bool = False,
) -> dict:
    """
    Backward-compatible facade: delegates business analysis to ConversionAnalyzer,
    presentation to ConversionReporter, keeps Telegram transport in this module.
    """
    if send_telegram and not CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID_ANALIZ не задан — отправка в Telegram отключена")
        send_telegram = False

    logger.info(f"[run] 🚀 Начало анализа: {os.path.basename(conv_file)}")

    snapshot = get_snapshot_v2(force_sync=rules_force_sync)

    special_folder = os.getenv("DROPBOX_SPECIAL_PATH", "/Ostin/platform/special")
    dropbox_special_file = os.path.join(special_folder, "special_cards.xlsx")
    local_special_path = os.path.join(tempfile.gettempdir(), "special_cards.xlsx")

    special_state = SpecialCardsState.empty()
    try:
        special_state = _analyzer.load_special_cards_state(
            dropbox_special_file=dropbox_special_file,
            local_special_path=local_special_path,
            download_fn=download_file,
        )
        if special_state.special_loaded:
            _notify_special_cards_loaded(special_state, send_telegram=send_telegram)
        else:
            _notify_special_cards_load_failure(send_telegram=send_telegram)
    except Exception as e:
        logger.warning(f"[run] ⚠️ Ошибка при загрузке/чтении special_cards.xlsx: {e}")
        _notify_special_cards_load_failure(send_telegram=send_telegram, error=True)
        special_state = SpecialCardsState.empty()

    _notify_special_cards_latest_date(special_state, send_telegram=send_telegram)

    analysis = _analyzer.analyze(
        conv_file=conv_file,
        card_files=card_files,
        col_mapping=col_mapping,
        snapshot=snapshot,
        special_state=special_state,
    )

    summary = analysis.summary
    problem = analysis.problem_cards

    wb = None
    report_path = None
    if generate_excel:
        wb = render_excel(analysis)
        current_date = now_msk().strftime("%d.%m.%Y")
        base_name = f"report_{os.path.splitext(os.path.basename(conv_file))[0]}_({current_date}).xlsx"
        report_path = os.path.join(tempfile.gettempdir(), base_name)
        logger.info(f"[run] Листы отчёта: {wb.sheetnames}")
        wb.save(report_path)
        logger.info(f"[run] 📁 Отчёт сохранён: {report_path}")

    if send_telegram:
        telegram = render_telegram(analysis, conv_file)

        if telegram.problem_columns_error:
            send_message_sync(telegram.problem_columns_error, chat_id=CHAT_ID)
        elif telegram.problem_messages:
            _send_telegram_messages(telegram.problem_messages)
            logger.info(f"[telegram] Отправлен список {telegram.problem_cards_count} карт на отключение.")
        elif telegram.no_problems_message:
            send_message_sync(telegram.no_problems_message, chat_id=CHAT_ID)

        try:
            send_message_sync(telegram.summary_message, chat_id=CHAT_ID)
        except Exception as e:
            logger.exception(f"[run] Ошибка при отправке Telegram summary: {e}")

        if generate_excel and report_path and os.path.exists(report_path):
            try:
                send_file_sync(report_path, caption=telegram.file_caption, chat_id=CHAT_ID)
                logger.info(f"[run] Файл отчёта отправлен в Telegram: {report_path}")
            except Exception as e:
                logger.exception(f"[run] Ошибка при отправке отчёта в Telegram: {e}")

    try:
        maybe_enqueue_wallet_editor_from_problem_cards(problem, conv_file=conv_file)
    except Exception as e:
        logger.exception(f"[run] Conversion → Wallet Editor bridge error (ignored): {e}")

    return {
        "summary": summary,
        "workbook": wb,
        "problem_cards": problem,
        "report_path": report_path,
    }
