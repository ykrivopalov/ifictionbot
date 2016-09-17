from concurrent.futures import CancelledError
import asyncio
import logging
import signal
import sys

from telepot.aio.delegate import create_open, pave_event_space, per_chat_id
import telepot

from . import session

if __name__ == "__main__":
    token = sys.argv[1]
    data_path = sys.argv[2]
    print('data path: ' + data_path)

    formatter = logging.Formatter('%(asctime)s | %(levelname)s | %(message)s')

    stream_logger = logging.StreamHandler()
    stream_logger.setFormatter(formatter)
    stream_logger.setLevel(logging.DEBUG)

    logging.basicConfig(level=logging.DEBUG, handlers=[stream_logger])

    loop = asyncio.get_event_loop()
    registry = session.SessionRegistry()
    bot = telepot.aio.DelegatorBot(
        token,
        [pave_event_space()(
            per_chat_id(), create_open, session.Session, data_path, loop,
            registry, timeout=20 * 60)],
        loop
    )
    loop.create_task(bot.message_loop())

    def sigint_handler():
        logging.info('SIGINT')
        try:
            registry.close_all()
        except Exception as e:
            logging.error(e)
        finally:
            loop.stop()

    loop.add_signal_handler(signal.SIGINT, sigint_handler)

    logging.info('Listening ...')
    try:
        loop.run_forever()
    except CancelledError:
        logging.info('Cancelled')
    else:
        logging.info('Completed')
    finally:
        loop.close()

