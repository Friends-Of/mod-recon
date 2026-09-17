"""Background entrypoint with rotating local logs."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

import watch

if __name__ == '__main__':
    root = Path(__file__).resolve().parent
    os.chdir(root)
    (root / 'data').mkdir(exist_ok=True)
    handler = RotatingFileHandler(root / 'data' / 'service.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', handlers=[handler])
    logging.info('Reforger Watch starting')
    try:
        watch.main()
    except Exception:
        logging.exception('Service stopped unexpectedly')
        raise
