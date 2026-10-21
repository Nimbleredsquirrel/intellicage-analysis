from multiprocessing import freeze_support
import sys

if __name__ in {'__main__', '__mp_main__'}:
    from intellicage.desktop import chrome_worker, prepare_stdio, check_exports

    if not chrome_worker():
        prepare_stdio()
        freeze_support()
        if len(sys.argv) == 3 and sys.argv[1] == '--check-exports':
            check_exports(sys.argv[2])
        else:
            from intellicage.interface import main

            main(['--desktop', *sys.argv[1:]] if getattr(sys, 'frozen', False) else None)
