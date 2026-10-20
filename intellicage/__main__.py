import argparse
import tempfile
from pathlib import Path

from .pipeline import describe, run
from .project import Project


def main(argv=None):
    parser = argparse.ArgumentParser(prog='intellicage', description='IntelliCage analysis pipeline')
    parser.add_argument('config', nargs='?', default='config.yaml', help='saved .icage project or batch settings')
    parser.add_argument('--only', nargs='+', help='analysis names or metric columns')
    parser.add_argument('--list', action='store_true', help='list available tables and columns')
    args = parser.parse_args(argv)
    path = Path(args.config)
    if path.suffix.lower() == '.icage':
        with tempfile.TemporaryDirectory(prefix='intellicage-batch-') as folder:
            project = Project.open(path.read_bytes(), Path(folder))
            cfg = project.configuration()
            return describe(path, configuration=cfg) if args.list else run(path, args.only, configuration=cfg)
    return describe(args.config) if args.list else run(args.config, args.only)


if __name__ == '__main__':
    main()
