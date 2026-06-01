import threading
from automation.worker import worker_loop
from automation.tg_receiver import run_receiver


def main():

    threading.Thread(target=worker_loop, daemon=True).start()

    try:
        run_receiver()
    except Exception as e:
        print("❌ Receiver упал:", e)
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
