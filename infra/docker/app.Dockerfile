# Playwright's Python image ships Chromium and its system libraries.
# Keep this tag in step with the playwright pin in backend/pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_BREAK_SYSTEM_PACKAGES=1

# pg_dump for the daily backup into data/backups (functions/backup.py); noble ships 16.
# age encrypts that dump to a public key when JAP_BACKUP_AGE_RECIPIENT is set (IDEA-11):
# without it here, live would fall back to writing every user's data to disk in the clear.
#
# The upgrade takes the security fixes Ubuntu has released since Playwright built its image
# (apt and libbrotli1 were HIGH in the CI scan); the image is only as patched as its last
# build, so the CI image check is what notices when that has drifted again.
#
# virtualenv and filelock come with the base image, not from JAP, and nothing here uses
# them; nor does virtualenv's wheel cache, which held old certifi, msgpack, setuptools and
# urllib3. Removing them is cleaner than upgrading packages the lock does not pin.
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends postgresql-client age \
    && rm -rf /var/lib/apt/lists/* \
    && (pip uninstall -y virtualenv filelock || true) \
    && rm -rf /root/.cache/virtualenv

WORKDIR /app

# Dependencies first so code changes don't reinstall them. From the lock, not pyproject:
# --require-hashes installs the exact builds backend/requirements.lock names and refuses
# anything else, so the image a version tag produces is the same image tomorrow, and a
# package that is republished under its own version cannot slip into a rebuild (IDEA-23).
# After changing a dependency in pyproject.toml, run `make lock`.
#
# pip is removed once it has done that. Nothing in the running image installs anything, and
# pip carries its own copies of msgpack, setuptools and urllib3 under pip/_vendor, which
# trail the fixed releases and were the last HIGH findings in the CI image scan.
COPY backend/pyproject.toml backend/requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip uninstall -y pip

COPY backend/ ./

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
