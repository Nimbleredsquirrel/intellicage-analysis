from multiprocessing import freeze_support
import sys

if __name__ in {'__main__', '__mp_main__'}:
    from intellicage.desktop import chrome_worker, prepare_stdio, check_exports

    if not chrome_worker():
        prepare_stdio()
        try:
            freeze_support()
        except (Exception, SystemExit) as error:
            if isinstance(error, SystemExit) and error.code in (0, None): raise
            from intellicage.windows_startup import report_error

            report_error(error)
            raise SystemExit(1) from error
        if len(sys.argv) == 3 and sys.argv[1] == '--check-exports':
            check_exports(sys.argv[2])
        elif len(sys.argv) == 3 and sys.argv[1] == '--check-error-dialog':
            from intellicage.desktop import check_error_dialog

            check_error_dialog(sys.argv[2])
        else:
            from intellicage.windows_startup import application_session

            with application_session() as launch:
                if launch:
                    from intellicage.interface import main

                    main(['--desktop', *sys.argv[1:]] if getattr(sys, 'frozen', False) else None)
