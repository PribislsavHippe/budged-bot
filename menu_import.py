"""Загрузить меню из PDF в базу с компьютера: `.venv/bin/python menu_import.py файл.pdf [файл.pdf …]`.

На бесплатном Render разбор PDF не помещается в память, поэтому файлы разбираются здесь, а в базу уходят готовые
карточки и фото — тем же путём, что и при загрузке через бота (`menu_store.import_pdf`): новая версия файла заменяет
старую, старую можно вернуть неделю. Нужны SUPABASE_URL и серверный ключ SUPABASE_KEY в `.env` (как на Render).
"""
import asyncio
import hashlib
import logging
import os
import sys

from dotenv import load_dotenv


async def run(paths):
    import menu_store
    owner = int(os.getenv('ADMIN_ID') or 0)
    for path in paths:
        data = open(path, 'rb').read()
        source = menu_store.source_name(os.path.basename(path))
        key = 'file:' + hashlib.sha256(data).hexdigest()[:40]
        result = await menu_store.import_pdf(owner, key, source, data)
        if result['status'] == 'ok':
            skipped = ', '.join(str(s['page']) for s in result['skipped']) or 'нет'
            print(f"{source}: {result['count']} позиций, с фото {result['photos']}; пропущены страницы: {skipped}")
        else:
            print(f"{source}: {result['status']}")


def main(paths):
    load_dotenv()
    logging.disable(logging.WARNING)
    # Одно соединение с базой на все файлы: очередь запросов в db привязана к одному циклу asyncio.
    asyncio.run(run(paths))


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
