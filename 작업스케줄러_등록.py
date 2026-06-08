"""
작업 스케줄러 등록 — 더블클릭으로 실행하세요
관리자 권한 UAC 창이 뜨면 [예] 클릭
"""
import ctypes, sys, os, subprocess
from pathlib import Path

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

# 관리자 권한 없으면 UAC 요청 후 재실행
if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable,
        f'"{os.path.abspath(__file__)}"', None, 1
    )
    sys.exit()

# ── 관리자 권한으로 실행 중 ──
BASE_DIR  = Path(__file__).parent
bat_path  = str(BASE_DIR / "백필_자동실행.bat")
work_dir  = str(BASE_DIR)
task_name = "포토이즘백필"

print(f"작업 등록 중...")
print(f"  실행 파일: {bat_path}")
print(f"  간격: 20분")

ps = f"""
$t = '{task_name}'
$b = '{bat_path.replace(chr(39), "''")}'
$w = '{work_dir.replace(chr(39), "''")}'
Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
$action   = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $b + '"') -WorkingDirectory $w
$trigger  = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 20) -Once -At (Get-Date).AddMinutes(1)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 19) -MultipleInstances IgnoreNew -StartWhenAvailable
Register-ScheduledTask -TaskName $t -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force
"""

result = subprocess.run(
    ["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps],
    capture_output=True, text=True, encoding="utf-8", errors="replace"
)

print(result.stdout)
if result.returncode != 0 and result.stderr:
    print("오류:", result.stderr[:500])
    input("\n엔터를 눌러 닫기...")
else:
    print(f"\n[완료] '{task_name}' 등록 성공!")
    print("20분마다 자동 실행됩니다.")
    input("\n엔터를 눌러 닫기...")
