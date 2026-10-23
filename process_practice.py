
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-c", "print(5 + 5)"],
    capture_output=True,
    text=True,
    timeout=5
)

print("Output:", result.stdout)
print("Error:", result.stderr)
print("Exit Code:", result.returncode)
