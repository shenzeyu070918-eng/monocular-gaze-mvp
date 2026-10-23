"""System SAPI COM object; constant script, communication text only on stdin."""
def command():
    return 'powershell.exe',['-NoProfile','-NonInteractive','-Command',
        '[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false); '
        '$text = [Console]::In.ReadToEnd(); '
        '$voice = New-Object -ComObject SAPI.SpVoice; [void]$voice.Speak($text, 0)']
