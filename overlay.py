"""Run the M3a uncalibrated desktop overlay, independent of the web service."""
if __name__=='__main__':
    from overlay.app import main
    raise SystemExit(main())
