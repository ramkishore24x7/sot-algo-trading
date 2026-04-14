import logging
import threading

from enum import Enum, auto
from queue import Queue
from utils.demat import Demat

class TradeAction(Enum):
    ENTER = auto()
    SQUAREOFF = auto()
    SQUAREOFF_AGGRESSIVE = auto()
    SQUAREOFF_LAZY = auto()
    AVERAGE = auto()
    BOOK_TARGET1 = auto()
    BOOK_TARGET2 = auto()
    BOOK_TARGET3 = auto()
    EXIT_VIA_TELEGRAM = auto()

class TradeManager:
    def __init__(self, demats,logger:logging=None):
        """
        Initialize the TradeManager with a list of Demat objects.

        :param demats: List of Demat instances
        """
        if not isinstance(demats, list):
            raise TypeError("demats must be a list of Demat instances")

        if not all(isinstance(demat, Demat) for demat in demats):
            raise TypeError("All elements in demats must be instances of Demat")

        self.demats = demats
        self.logger = logger
    
    def _position_worker(self, demat, action, position, result_queue, price=None):
        """
        Worker function for handling positions.
        """
        if action == TradeAction.ENTER:
            result = demat.take_position(position, price)
        elif action == TradeAction.EXIT_VIA_TELEGRAM:
            result = demat.exit_position_via_telegram(position, price)
        elif action == TradeAction.SQUAREOFF:
            result = demat.square_off_position(position, price)
        elif action == TradeAction.SQUAREOFF_AGGRESSIVE:
            result = demat.square_off_position_aggressive_trail(position, price)
        elif action == TradeAction.SQUAREOFF_LAZY:
            result = demat.square_off_position_lazy_trail(position, price)
        elif action == TradeAction.AVERAGE:
            result = demat.average_position(position, price)
        elif action == TradeAction.BOOK_TARGET1:
            result = demat.book_target1(position, price)
        elif action == TradeAction.BOOK_TARGET2:
            result = demat.book_target2(position, price)
        elif action == TradeAction.BOOK_TARGET3:
            result = demat.book_target3(position, price)
        else:
            raise ValueError("Invalid action specified")

        return result_queue.put(result)

    def _handle_position(self, action, position, price=None):
        """
        Handle the threading and queueing for entering or exiting positions.
        """
        result_queue = Queue()
        threads = []
        for demat in self.demats:
            thread = threading.Thread(target=self._position_worker, args=(demat, action, position, result_queue, price))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        processed_positions = []
        while not result_queue.empty():
            result = result_queue.get()
            if result is not None:
                processed_positions.append(result)
        self.logger.warning(f"processed_positions:  {processed_positions}")
        return processed_positions

    def enter_position(self, position, price):
        """
        Enter a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.ENTER, position, price)

    def exit_position_via_telegram(self, position, price=None):
        """
        Exit a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.EXIT_VIA_TELEGRAM, position, price)
    
    def average_position(self, position, price):
        """
        Average a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.AVERAGE, position, price)

    def book_target1(self, position, price):
        """
        book_target1 a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.BOOK_TARGET1, position, price)

    def book_target2(self, position, price):
        """
        book_target2 a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.BOOK_TARGET2, position, price)

    def book_target3(self, position, price):
        """
        book_target3 a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.BOOK_TARGET3, position, price)

    def square_off_position(self, position, price):
        """
        square_off_position a position on all Demat accounts.
        """
        return self._handle_position(TradeAction.SQUAREOFF, position, price)

    def square_off_position_aggressive_trail(self, position, price):
        """
        square_off_position a position on all aggressive_trail Demat accounts.
        """
        return self._handle_position(TradeAction.SQUAREOFF_AGGRESSIVE, position, price)
    
    def square_off_position_lazy_trail(self, position, price):
        """
        square_off_position a position on all aggressive_trail Demat accounts.
        """
        return self._handle_position(TradeAction.SQUAREOFF_LAZY, position, price)