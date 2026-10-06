#!/usr/bin/env python3
"""Opt-in real Codex check using only generated inputs, never user documents."""
import hashlib
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from errand.session import Session

def pdf(path, text):
    stream = f"BT /F1 18 Tf 50 750 Td ({text}) Tj ET".encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>",
               b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data, offsets = b"%PDF-1.4\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{number} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    data += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    path.write_bytes(data)

class Check:
    def __init__(self):
        self.events = []
        self.changed = threading.Condition()
        self.session = Session(self.event)
        self.allowed_directory = None

    def permitted_test_permissions(self, permissions):
        if (not self.allowed_directory or set(permissions) - {"network", "fileSystem"}
                or (permissions.get("network") or {}).get("enabled")):
            return False
        filesystem = permissions.get("fileSystem") or {}
        if set(filesystem) - {"read", "write", "entries", "globScanMaxDepth"}:
            return False
        paths = list(filesystem.get("read") or []) + list(filesystem.get("write") or [])
        for entry in filesystem.get("entries") or []:
            if entry["path"].get("type") != "path" or entry.get("access") not in {"read", "write"}:
                return False
            paths.append(entry["path"]["path"])
        return bool(paths) and all(Path(path).is_absolute() and Path(path).resolve().is_relative_to(self.allowed_directory)
                                   for path in paths)

    def permitted_test_command(self, command):
        if not self.allowed_directory:
            return False
        try:
            argv = shlex.split(command)
            if len(argv) == 3 and argv[0] in {"/bin/bash", "/bin/sh", "bash", "sh"} and argv[1] in {"-lc", "-c"}:
                argv = shlex.split(argv[2])
        except ValueError:
            return False
        expected = ["qpdf", "--empty", "--pages", str(self.allowed_directory / "a.pdf"),
                    str(self.allowed_directory / "b.pdf"), "--", str(self.allowed_directory / "merged.pdf")]
        return argv == expected or argv == ["/usr/bin/qpdf"] + expected[1:]

    def event(self, kind, data):
        with self.changed:
            self.events.append((kind, data))
            self.changed.notify_all()
        if kind in {"assistant", "error"}:
            print(kind + ": " + data.get("text", data.get("message", "")), flush=True)
        if kind == "request":
            method = data["method"]
            permissions = data["params"].get("permissions", {})
            if method == "item/permissions/requestApproval" and self.permitted_test_permissions(permissions):
                print("Allowing turn-scoped permissions inside generated test directory only.", flush=True)
                self.session.answer(data["request_id"], {"permissions": permissions, "scope": "turn"})
                return
            if method == "item/commandExecution/requestApproval" and self.permitted_test_command(data["params"].get("command", "")):
                print("Allowing only the exact synthetic qpdf merge command once.", flush=True)
                self.session.answer(data["request_id"], {"decision": "accept"})
                return
            print("Unexpected approval/question; refusing unattended check.", flush=True)
            if method.endswith("requestApproval"):
                result = {"permissions": {}, "scope": "turn"} if "/permissions/" in method else {"decision": "cancel"}
                self.session.answer(data["request_id"], result)
            else:
                self.session.interrupt()

    def turn(self, text, model=None, effort=None):
        self.events.clear()
        self.session.send(text, model=model, effort=effort)
        deadline = time.monotonic() + 180
        with self.changed:
            while not any(k == "state" and not d["busy"] for k, d in self.events):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.session.interrupt()
                    raise RuntimeError("Live check timed out")
                self.changed.wait(min(remaining, 10))
        errors = [d["message"] for k, d in self.events if k == "error"]
        if errors:
            raise RuntimeError("; ".join(errors))
        answers = [d["text"] for k, d in self.events if k == "assistant"]
        if not answers:
            raise RuntimeError("No assistant response")
        return "\n".join(answers)

def main():
    check = Check()
    try:
        check.turn("『丁寧』の類義語を3つ、日本語で挙げてください。ツールは使わないでください。")
        thread = check.session.thread_id
        check.turn("その中から、仕事の依頼文に適したものを1つ選んでください。ツールは使わないでください。")
        assert check.session.thread_id == thread
        print("PASS: same-thread conversation", flush=True)
        check.session.reset()
        with tempfile.TemporaryDirectory(prefix="errand-pdf-check-") as directory:
            folder = Path(directory)
            for name in ("a", "b"):
                pdf(folder / f"{name}.pdf", f"Synthetic test {name}")
            hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.glob("*.pdf")}
            check.allowed_directory = folder.resolve()
            command = shlex.join(["qpdf", "--empty", "--pages", str(folder / "a.pdf"), str(folder / "b.pdf"),
                                  "--", str(folder / "merged.pdf")])
            check.turn(f"{folder}/a.pdf と {folder}/b.pdf を、この順で {folder}/merged.pdf に連結してください。"
                       "これは合成した検証用PDFです。既存のファイルを変更・削除せず、"
                       "request_permissionsが使えればこの合成PDFのディレクトリ内への書き込み権限だけを要求し、"
                       "使えなければ次のコマンドそのものの実行承認を求めてください。"
                       f"連結コマンドを変更したり他の処理と連結したりせず実行してください: {command}\n"
                       "qpdfで出力が2ページあることとPDF構造に問題がないことを確認してください。")
            assert all(hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
            result = subprocess.check_output(["qpdf", "--show-npages", str(folder / "merged.pdf")], text=True).strip()
            assert result == "2", result
            subprocess.run(["qpdf", "--check", str(folder / "merged.pdf")], check=True)
            print("PASS: 2-page PDF merge; both originals preserved", flush=True)
    finally:
        check.session.close()

if __name__ == "__main__":
    main()
