from multiprocessing import freeze_support
import sys

if __name__ in {'__main__', '__mp_main__'}:
    freeze_support()
    from intellicage.interface import main

    main(['--desktop', *sys.argv[1:]] if getattr(sys, 'frozen', False) else None)
