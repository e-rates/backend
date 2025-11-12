@echo off
REM Batch file to import shapefiles with correct PROJ/GDAL configuration
REM Usage: import_parcels.bat [shapefile_path] [options]

REM Set PROJ and GDAL environment variables
set PROJ_LIB=D:\projects\e-rates\env\Lib\site-packages\osgeo\data\proj
set GDAL_DATA=D:\projects\e-rates\env\Lib\site-packages\osgeo\data\gdal

REM Check if shapefile path is provided
if "%~1"=="" (
    echo Error: Shapefile path is required
    echo.
    echo Usage: import_parcels.bat "path\to\shapefile.shp" --user admin --ref-field PARCEL_NO [--dry-run]
    echo.
    echo Example:
    echo   import_parcels.bat "D:\WGS Shapefiles\WGS Shapefiles\Endarasha_Settlement_Scheme.shp" --user admin --ref-field PARCEL_NO --dry-run
    exit /b 1
)

REM Activate virtual environment
call env\Scripts\activate.bat

REM Run the import command with all provided arguments
python manage.py import_shapefiles %*

REM Deactivate virtual environment
deactivate
