import argparse

from .pipeline import describe, run


def main(argv=None):
    parser = argparse.ArgumentParser(prog='intellicage', description='IntelliCage analysis pipeline')
    parser.add_argument('config', nargs='?', default='config.yaml')
    parser.add_argument('--only', nargs='+', help='analysis names or metric columns')
    parser.add_argument('--list', action='store_true', help='list available tables and columns')
    args = parser.parse_args(argv)
    return describe(args.config) if args.list else run(args.config, args.only)


if __name__ == '__main__':
    main()
