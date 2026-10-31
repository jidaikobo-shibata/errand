"""Per-turn monotonic response timings, independent of product and UI."""
from dataclasses import dataclass


@dataclass
class ResponseTiming:
    started_at: float
    first_text_at: float | None = None
    finished_at: float | None = None
    status: str = 'completed'

    def text_received(self, now):
        if self.first_text_at is None and self.finished_at is None:
            self.first_text_at = max(self.started_at, now)

    def finish(self, now, status='completed'):
        if self.finished_at is None:
            self.finished_at = max(self.started_at, self.first_text_at or self.started_at, now)
            self.status = status

    def display(self):
        if self.finished_at is None:
            return '', ''
        elapsed = self.finished_at - self.started_at
        prefix = {'interrupted': '中断 · ', 'failed': '失敗 · ', 'incomplete': '途中まで · '}.get(self.status, '')
        text = f'{prefix}{elapsed:.1f}秒'
        details = [f'送信受付から終了まで: {elapsed:.1f}秒']
        if self.first_text_at is not None:
            first = self.first_text_at - self.started_at
            text += f' · 初字 {first:.1f}秒'
            details += [f'送信受付から最初の文字受信まで: {first:.1f}秒',
                        f'最初の文字受信から終了まで: {self.finished_at - self.first_text_at:.1f}秒']
        details.append('接続・ツール実行・承認待ちを含む時間です。')
        return text, '\n'.join(details)
