import os
import sys
import time
import math
import random
import itertools
import warnings
import numpy as np
from typing import List, Dict, Tuple, Sequence, Callable
from functools import partial
from multiprocessing.context import BaseContext
from multiprocessing.pool import ThreadPool

import torch

import pbt.member
from .trainer import Trainer
from .evaluator import Evaluator
from .worker import Worker, Job
from .member import Checkpoint, MissingStateError

VERBOSE = True

# various settings for reproducibility
# set random state 
random.seed(0)
np.random.seed(0)
torch.manual_seed(0)
# set torch settings
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
torch.backends.cudnn.enabled = True
# multiprocessing
torch.multiprocessing.set_sharing_strategy('file_descriptor')

class TrainingService(object):
    def __init__(self, context : BaseContext, trainer : Trainer, evaluator : Evaluator,
            devices : List[str] = ['cpu'], n_jobs : int = 1, threading : bool = False, verbose : bool = False):
        super().__init__()
        if n_jobs < len(devices):
            raise ValueError("n_jobs must be larger or equal the number of devices.")
        self.context = context
        self.trainer = trainer
        self.evaluator = evaluator
        self.devices = devices
        self.n_jobs = n_jobs
        self.threading = threading
        self.verbose = verbose
        self._workers : List[Worker] = list()
        self._send_queue = context.Queue()
        self._return_queue = context.Queue()
        self.__seed = 0
    
    def _create_worker(self, id, device) -> Worker:
        worker = Worker(id=id, end_event=self._end_event, receive_queue=self._send_queue, return_queue=self._return_queue,
            trainer=self.trainer, evaluator=self.evaluator, device = device, random_seed = self.__seed, verbose = self.verbose)
        self.__seed += 1
        return worker

    def start(self):
        if any(worker.is_alive() for worker in self._workers):
            raise Exception("Service is already running. Consider calling stop() when service is not in use.")
        self._end_event = self.context.Event()
        for id, device in zip(range(self.n_jobs), itertools.cycle(self.devices)):
            worker = self._create_worker(id, device)
            worker.start()
            self._workers.append(worker)

    def stop(self):
        if not self._workers or all(worker.is_alive() for worker in self._workers):
            warnings.warn("Service is not running.")
            return
        for worker in self._workers:
            worker.close()
            worker.join()
        self._end_event.set()
    
    def terminate(self):
        if not self._workers or all(worker.is_alive() for worker in self._workers):
            warnings.warn("Service is not running.")
            return
        for worker in self._workers:
            worker.terminate()
            worker.join()
        self._end_event.set()

    def train(self, candidates : Sequence, step_size : int):
        for checkpoints in candidates:
            job = Job(checkpoints, step_size)
            self._send_queue.put(job)
        for _ in candidates:
            yield self._return_queue.get()