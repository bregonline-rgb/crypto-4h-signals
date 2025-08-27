from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.textinput import TextInput
from kivy.clock import Clock
import threading
import ccxt
import pandas as pd
import time

TIMEFRAME = '4h'
LIMIT = 300

class MainUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation='vertical', **kwargs)
        self.input = TextInput(text='ETH/USDT BTC/USDT SOL/USDT', size_hint_y=None, height=40)
        self.add_widget(self.input)
        self.btn = Button(text='Refresh Signals', size_hint_y=None, height=50)
        self.btn.bind(on_press=self.on_refresh)
        self.add_widget(self.btn)
        self.output = Label(text='Press Refresh to fetch 4h signals', halign='left', valign='top')
        self.output.bind(size=self.output.setter('text_size'))
        self.add_widget(self.output)

    def on_refresh(self, *args):
        self.btn.disabled = True
        threading.Thread(target=self.fetch_and_update, daemon=True).start()

    def fetch_and_update(self):
        symbols = [s.strip() for s in self.input.text.split() if s.strip()]
        exchange = ccxt.binance({'enableRateLimit': True})
        results = []
        for s in symbols:
            try:
                raw = exchange.fetch_ohlcv(s, timeframe=TIMEFRAME, limit=LIMIT)
                df = pd.DataFrame(raw, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
                df['ts'] = pd.to_datetime(df['ts'], unit='ms', utc=True)
                df = df.set_index('ts')
                # use a tiny subset of compute_signals logic (for speed)
                last = df.iloc[-1]
                sma50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                sig = 'HOLD'
                reason = f'close {last["close"]:.4f}'
                if last['close'] > sma50:
                    sig = 'BUY'
                    reason += ' | price>EMA50'
                else:
                    sig = 'SELL'
                    reason += ' | price<EMA50'
                results.append(f"{s}: {sig} ({reason})")
            except Exception as e:
                results.append(f"{s}: ERROR {e}")
        out = '\\n'.join(results)
        def update_label():
            self.output.text = out
            self.btn.disabled = False
        Clock.schedule_once(lambda dt: update_label())

class SignalApp(App):
    def build(self):
        return MainUI()

if __name__ == '__main__':
    SignalApp().run()
