"""Optional local Chrome print-to-PDF; absence never prevents HTML generation."""
from pathlib import Path
import subprocess,tempfile


def export_pdf(artifact,*,executable=None,runner=subprocess.run):
    if executable is None:
        from overlay.galaxy_browser import chrome_executable
        executable=chrome_executable()
    if not executable:raise RuntimeError('未找到Chrome；请打开离线HTML，用浏览器打印→另存为PDF')
    path=artifact.directory/'report.pdf'
    with tempfile.TemporaryDirectory(prefix='gaze-report-print-') as profile:
        result=runner([str(executable),'--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--no-first-run','--no-default-browser-check','--user-data-dir='+profile,'--no-pdf-header-footer','--print-to-pdf='+str(path),artifact.html.resolve().as_uri()],capture_output=True,timeout=45)
    if result.returncode or not path.exists() or path.stat().st_size==0:raise RuntimeError('PDF导出失败；HTML完整保留，可从浏览器打印')
    return path
