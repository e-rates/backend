#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

# Fix GDAL/PROJ database version mismatch - use OSGEO's proj.db
# This prevents errors from PostgreSQL's outdated proj.db being loaded
if 'PROJ_LIB' not in os.environ:
    import pathlib
    site_packages = pathlib.Path(__file__).parent / 'env' / 'Lib' / 'site-packages'
    proj_lib = site_packages / 'osgeo' / 'data' / 'proj'
    if proj_lib.exists():
        os.environ['PROJ_LIB'] = str(proj_lib)


def main():
    """Run administrative tasks."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
