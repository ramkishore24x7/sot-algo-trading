import threading
import time
import requests
import logging

logger = logging.getLogger(__name__)


class PriceDispatcher:
    """
    Single polling loop that fans out price updates to multiple subscribers.

    Problem it solves:
        With N active trades, each SOT bot was polling getLTP() via HTTP
        independently — N processes × 10 req/sec = N×10 HTTP calls/sec to the
        same Flask websocket server.

    How it works:
        One PriceDispatcher polls once per tick and calls every registered
        on_price(cmp) callback. 10 trades → still 1 HTTP call per tick.

    Usage (single trade, SOT_BOTv8):
        dispatcher = PriceDispatcher("NSE:NIFTY50-INDEX", port=4001)
        dispatcher.subscribe(handler.on_price)
        dispatcher.start()
        handler.wait()
        dispatcher.stop()

    Usage (multi-trade coordinator, future):
        dispatcher = PriceDispatcher("NSE:NIFTY50-INDEX", port=4001)
        dispatcher.start()
        for trade in trades:
            dispatcher.subscribe(trade.on_price)   # register on entry
            ...                                     # deregister on exit
    """

    def __init__(self, instrument: str, port: int, poll_interval: float = 0.1):
        self.instrument = instrument
        self.port = port
        self.poll_interval = poll_interval
        self._subscribers: list = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Subscription management
    # ------------------------------------------------------------------

    def subscribe(self, callback):
        """Register a callback to receive price ticks. Thread-safe."""
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
        logger.debug(f"PriceDispatcher[{self.instrument}]: +subscriber (total={self.subscriber_count})")

    def unsubscribe(self, callback):
        """Deregister a callback. Thread-safe. Safe to call if not subscribed."""
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        logger.debug(f"PriceDispatcher[{self.instrument}]: -subscriber (total={self.subscriber_count})")

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the background polling thread. Safe to call multiple times."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name=f"PriceDispatcher-{self.instrument}",
        )
        self._thread.start()
        logger.info(f"PriceDispatcher started: {self.instrument} @ port {self.port}, interval={self.poll_interval}s")

    def stop(self):
        """Stop the polling thread."""
        self._running = False
        logger.info(f"PriceDispatcher stopped: {self.instrument}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_ltp(self) -> float:
        url = f"http://localhost:{self.port}/ltp?instrument={self.instrument}"
        try:
            resp = requests.get(url, timeout=2)
            data = resp.json()
            return float(data) if data not in (None, -1) else -1.0
        except Exception as e:
            logger.debug(f"PriceDispatcher fetch error [{self.instrument}]: {e}")
            return -1.0

    def _loop(self):
        while self._running:
            cmp = self._fetch_ltp()
            if cmp != -1.0:
                # Snapshot the list so subscribe/unsubscribe mid-tick is safe
                with self._lock:
                    callbacks = list(self._subscribers)
                for cb in callbacks:
                    try:
                        cb(cmp)
                    except Exception as e:
                        logger.error(f"PriceDispatcher: error in subscriber callback: {e}")
            time.sleep(self.poll_interval)
