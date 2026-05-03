import queue
import subprocess
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk


class AiderDeskApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Aider Desk")
        self.root.geometry("1100x720")

        self.proc = None
        self.output_queue = queue.Queue()

        self._build_ui()
        self._poll_output()

    def _build_ui(self):
        container = ttk.Frame(self.root, padding=10)
        container.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(container)
        top.pack(fill=tk.X)

        ttk.Label(top, text="Model").pack(side=tk.LEFT)
        self.model = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.model, width=30).pack(side=tk.LEFT, padx=(8, 16))

        ttk.Label(top, text="Files").pack(side=tk.LEFT)
        self.files = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.files, width=45).pack(side=tk.LEFT, padx=(8, 16), fill=tk.X, expand=True)

        self.start_btn = ttk.Button(top, text="Start session", command=self.start_session)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = ttk.Button(top, text="Stop", command=self.stop_session, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        self.output = scrolledtext.ScrolledText(container, wrap=tk.WORD, height=32)
        self.output.pack(fill=tk.BOTH, expand=True, pady=(10, 10))

        bottom = ttk.Frame(container)
        bottom.pack(fill=tk.X)

        self.prompt = tk.StringVar()
        entry = ttk.Entry(bottom, textvariable=self.prompt)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.bind("<Return>", lambda _e: self.send_message())

        send_btn = ttk.Button(bottom, text="Send", command=self.send_message)
        send_btn.pack(side=tk.LEFT, padx=(8, 0))

    def append_output(self, text):
        self.output.insert(tk.END, text)
        self.output.see(tk.END)

    def start_session(self):
        if self.proc is not None:
            return

        cmd = ["aider", "--pretty", "--yes-always"]
        if self.model.get().strip():
            cmd.extend(["--model", self.model.get().strip()])
        if self.files.get().strip():
            cmd.extend(self.files.get().strip().split())

        self.append_output(f"\n$ {' '.join(cmd)}\n")
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)

        threading.Thread(target=self._reader_thread, daemon=True).start()

    def _reader_thread(self):
        assert self.proc is not None
        for line in self.proc.stdout:
            self.output_queue.put(line)
        self.output_queue.put("\n[session ended]\n")
        self.proc = None

    def _poll_output(self):
        while True:
            try:
                line = self.output_queue.get_nowait()
            except queue.Empty:
                break
            self.append_output(line)

        if self.proc is None:
            self.start_btn.configure(state=tk.NORMAL)
            self.stop_btn.configure(state=tk.DISABLED)

        self.root.after(100, self._poll_output)

    def send_message(self):
        text = self.prompt.get().strip()
        if not text or self.proc is None or self.proc.stdin is None:
            return
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()
        self.append_output(f"\n> {text}\n")
        self.prompt.set("")

    def stop_session(self):
        if self.proc is None:
            return
        self.proc.terminate()


def desktop_gui_main():
    root = tk.Tk()
    app = AiderDeskApp(root)
    app.append_output("Aider Desk desktop GUI ready. Start a session to begin.\n")
    root.mainloop()


if __name__ == "__main__":
    desktop_gui_main()
