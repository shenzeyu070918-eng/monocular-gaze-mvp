"""Optional local Chrome print-to-PDF; absence never prevents HTML generation."""
from pathlib import Path
import os,subprocess,tempfile


def export_pdf(artifact,*,executable=None,runner=subprocess.run):
    if executable is None:
        from overlay.galaxy_browser import chrome_executable
        executable=chrome_executable()
    if not executable:raise RuntimeError('未找到Chrome；请打开离线HTML，用浏览器打印→另存为PDF')
    path=artifact.directory/'report.pdf'
    with tempfile.TemporaryDirectory(prefix='.gaze-report-print-',dir=artifact.directory) as temp:
        scratch=Path(temp)
        output=scratch/'report.pdf'
        try:
            result=runner([str(executable),'--headless=new','--disable-gpu','--disable-background-networking','--disable-component-update','--disable-sync','--disable-extensions','--no-first-run','--no-default-browser-check','--user-data-dir='+str(scratch/'profile'),'--no-pdf-header-footer','--print-to-pdf='+str(output),artifact.html.resolve().as_uri()],capture_output=True,timeout=45)
        except subprocess.TimeoutExpired:
            raise RuntimeError('PDF导出超时。可打开HTML后从浏览器打印') from None
        if result.returncode or not output.is_file() or output.stat().st_size==0:
            raise RuntimeError('Chrome未生成PDF。可打开HTML后从浏览器打印')
        os.replace(output,path)
    return path
