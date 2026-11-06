FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OWT_DATA_DIR=/data \
    OWT_FRONTEND_DIR=/app/frontend \
    PROJ_USER_WRITABLE_DIRECTORY=/opt/proj-grids

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# NOAA NADCON grids for an exact NAD27 to NAD83 shift in the lower 48 states.
ADD https://cdn.proj.org/us_noaa_conus.tif /opt/proj-grids/us_noaa_conus.tif
ADD https://cdn.proj.org/us_noaa_nadcon5_nad27_nad83_1986_conus.tif /opt/proj-grids/us_noaa_nadcon5_nad27_nad83_1986_conus.tif

COPY backend/app backend/app
COPY frontend frontend

RUN useradd --create-home owt && mkdir -p /data && chown owt /data && chmod -R a+r /opt/proj-grids
USER owt
VOLUME /data
EXPOSE 8000
WORKDIR /app/backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
