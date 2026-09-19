import subprocess

result = subprocess.run(['python', 'test.py'], capture_output=True, text=True)
print("exit code:", result.returncode)
print("stdout:", result.stdout)
print("stderr:", result.stderr)