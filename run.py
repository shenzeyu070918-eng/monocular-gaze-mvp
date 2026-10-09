"""Start a loopback-only service; open the displayed address in Chrome."""
import multiprocessing
import os
from pathlib import Path

if __name__ == '__main__':
    multiprocessing.freeze_support()
    os.chdir(Path(__file__).resolve().parent)
    import uvicorn
    print('请在 Chrome 打开 http://127.0.0.1:8765 ，等待页面显示「模型就绪」。', flush=True)
    uvicorn.run('app:app', host='127.0.0.1', port=8765, access_log=False)
