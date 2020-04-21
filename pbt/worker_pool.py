import os
import gc
import sys
import copy
import time
import math
import random
import warnings
import itertools
from functools import partial
from typing import List, Dict, Tuple, Sequence, Iterable, Callable, Union, Generator
from functools import partial
from dataclasses import dataclass
from multiprocessing.context import BaseContext
from multiprocessing.pool import ThreadPool

import torch
import numpy as np

import pbt.member
from .trainer import Trainer
from .evaluator import Evaluator
from .member import Checkpoint, MissingStateError
from .worker import STOP_FLAG, FailMessage, Trial, Worker
from .utils.cuda import get_gpu_memory_stats

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
torch.multiprocessing.set_sharing_strategy('file_system')

class WorkerPool(object):
    def __init__(self, trainer : Trainer, evaluator : Evaluator, tester : Evaluator = None, devices : Sequence[str] = ('cpu',),
            n_jobs : int = 1, verbose : int = 0):
        super().__init__()
        if n_jobs < len(devices):
            raise ValueError("n_jobs must be larger or equal the number of devices.")
        self.verbose = verbose
        self._cuda = any(device.startswith('cuda') for device in devices)
        self._context = torch.multiprocessing.get_context('spawn')
        self._manager = self._context.Manager()
        self._end_event = self._context.Event()
        send_queues = [self._context.Queue() for _ in devices]
        self._workers : List[Worker] = [
            Worker(id=id, end_event=self._end_event, receive_queue=send_queue,
                trainer=trainer, evaluator=evaluator, tester=tester, device=device, random_seed=id, verbose=verbose > 1)
            for id, send_queue, device in zip(range(n_jobs), itertools.cycle(send_queues), itertools.cycle(devices))]
        self._workers_iterator = itertools.cycle(self._workers)


    def _print(self, message : str) -> None:
        if self.verbose < 1:
            return
        full_message = f"{self.__class__}: {message}"
        print(full_message)

    def _print_gpu_memory_stats(self) -> None:
        if self.verbose < 2 or not self._cuda or os.name == 'nt':
            return
        memory_stats = get_gpu_memory_stats()
        memory_stats_formatted = (f"CUDA:{id} ({memory[0]}/{memory[1]}MB)" for id, memory in memory_stats.items())
        output = ', '.join(memory_stats_formatted)
        self._print(output)

    def _on_fail_message(self, message : FailMessage) -> None:
        # print info
        self._print(f"fail message received from worker {message.sender_id}: {message.text}.")
        if message.exception:
            self._print(f"exception: {message.exception}.")

    def _respawn(self, worker_id : int) -> None:
        # stop existing worker
        worker = self._workers[worker_id]
        self._print(f"terminating old worker with id {worker_id}...")
        self._stop_worker(worker)
        # spawn new worker
        self._print(f"spawning new worker with id {worker.id}...")
        self._workers[worker_id] = Worker(id=worker.id, end_event=self._end_event, receive_queue=worker.receive_queue, return_queue=self._return_queue,
            trainer=worker.trainer, evaluator=worker.evaluator, device = worker.device, random_seed = worker.id, verbose = self.verbose > 1)
        self._workers[worker_id].start()

    def _stop_worker(self, worker : Worker) -> None:
        worker.terminate()
        time.sleep(1.0) # give worker one second to stop
        worker.close()

    def start(self) -> None:
        if any(worker.is_alive() for worker in self._workers):
            raise Exception("service is already running. Consider calling stop() when service is not in use.")
        [worker.start() for worker in self._workers]

    def stop(self) -> None:
        if not any(worker.is_alive() for worker in self._workers):
            warnings.warn("service is not running.")
            return
        self._end_event.set()
        [worker.receive_queue.put(STOP_FLAG) for worker in self._workers]
        [worker.join() for worker in self._workers]
        [worker.close() for worker in self._workers]

    def train(self, candidates : Iterable[Union[Checkpoint, Tuple[Checkpoint,...]]], train_step_size : int, eval_step_size : int = None,
            train_shuffle : bool = False, eval_shuffle : bool = False) -> Generator[Union[Checkpoint, Tuple[Checkpoint,...]], None, None]:
        n_sent = 0
        n_returned = 0
        return_queue = self._manager.Queue()
        failed_workers = set()
        self._print(f"queuing candidates for training...")
        for checkpoints, worker in zip(candidates, self._workers_iterator):
            trial = Trial(return_queue=return_queue, checkpoints=checkpoints, train_step_size=train_step_size,
                eval_step_size=eval_step_size, train_shuffle=train_shuffle, eval_shuffle=eval_shuffle)
            worker.receive_queue.put(trial)
            n_sent += 1
        self._print(f"awaiting trained candidates...")
        while n_returned != n_sent and len(failed_workers) < len(self._workers):
            result = return_queue.get()
            self._print_gpu_memory_stats()
            if isinstance(result, FailMessage):
                self._on_fail_message(result)
                failed_workers.add(result.sender_id)
                continue
            n_returned += 1
            yield result
        if not return_queue.empty():
            raise Exception("return queue is not empty.")
        elif len(failed_workers) == len(self._workers):
            raise Exception("all workers failed.")
        elif n_returned < n_sent:
            if failed_workers:
                raise Exception(f"{len(failed_workers)} workers failed.")
            else:
                raise Exception(f"{n_sent - n_returned} candidates failed.")
        elif failed_workers:
            self._respawn(failed_workers)
        else:
            self._print("all candidates were trained successfully.")