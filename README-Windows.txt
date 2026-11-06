Offset Well Webtool - Windows laptop
====================================

START THE APP

1. Extract the ZIP file to a folder, for example Documents\OffsetWellWebtool.
2. Open the folder.
3. Double-click Start-OffsetWellWebtool.bat.
4. If Windows shows "Windows protected your PC", click "More info", then "Run anyway".
5. Wait for the setup. The first start takes 3 to 10 minutes.
6. Your browser opens the app at http://127.0.0.1:8000.

To stop the app, close the black window.
To start the app again, double-click Start-OffsetWellWebtool.bat. Later starts take a few seconds.


WHAT THE APP CAN DO ON A COMPANY NETWORK

The app needs these sites:
- State sites: gis.rrc.texas.gov, webapps.rrc.texas.gov, webapps.rrc.state.tx.us,
  gis.emnrd.nm.gov, ocdimage.emnrd.nm.gov, ecmc.state.co.us
- First start only: python.org (if the laptop has no Python), pypi.org,
  files.pythonhosted.org, cdn.proj.org

Without access to the Claude API (api.anthropic.com), these functions work:
- The offset search in TX, NM and CO
- Surveys in PDF files that have a text layer (most recent Compass and vendor reports)
- Plats and all source files, for review, and the Excel workbook and job package

These functions do not work without the Claude API:
- Surveys on scanned pages (PDF or TIF images)
- The page sort of New Mexico well files. The app still downloads the files.
  Open them from the "Source files" table.


IF SOMETHING FAILS

Read the message in the black window.

"The packages could not be installed":
  The network blocked pypi.org, or it needs a proxy. Open a Command Prompt in the folder and type:
    set HTTPS_PROXY=http://your-proxy:port
    Start-OffsetWellWebtool.bat
  Ask IT for the proxy address, or ask IT to allow pypi.org and files.pythonhosted.org.

"Python could not be installed":
  Install Python 3.11 or later from the company software center, or from
  https://www.python.org/downloads/ with "Install for me only". Then start the file again.

The port is in use:
  Open a Command Prompt in the folder and type:
    set OWT_PORT=8010
    Start-OffsetWellWebtool.bat


FILES

The app keeps Python, its packages, your jobs and the downloaded files in:
  %LOCALAPPDATA%\OffsetWellWebtool
Delete that folder to remove the app. Your jobs are in its "data" folder.
