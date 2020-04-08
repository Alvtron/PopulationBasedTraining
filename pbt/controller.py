import os
import sys
import math
import time
import random
import copy
import pickle
import shutil
import warnings
from typing import List, Dict, Sequence, Iterator
from functools import partial 
from collections import defaultdict
from multiprocessing.context import BaseContext
from dataclasses import dataclass
from multiprocessing.pool import ThreadPool

import torch
import torchvision
import torch.utils.data
from torch.utils.data import Dataset, DataLoader
from torch.utils.tensorboard import SummaryWriter
from torch.multiprocessing import Process
import matplotlib.pyplot as plt

import pbt.member
from .trainingservice import TrainingService
from .member import Checkpoint, Population, Generation
from .utils.date import get_datetime_string
from .hyperparameters import DiscreteHyperparameter, Hyperparameters
from .trainer import Trainer
from .evaluator import Evaluator
from .evolution import EvolveEngine
from .database import Database
from .garbage import GarbageCollector

class Controller(object):
    def __init__(
            self, population_size : int, hyper_parameters : Hyperparameters,
            trainer : Trainer, evaluator : Evaluator, evolver : EvolveEngine,
            loss_metric : str, eval_metric : str, loss_functions : dict, database : Database,
            step_size = 1, end_criteria : dict = {'score': 100.0},
            tensorboard_writer : SummaryWriter = None, detect_NaN : bool = False, history_limit : int = None,
            devices : List[str] = ['cpu'], n_jobs : int = -1, verbose : int = 1, logging : bool = True):
        assert step_size and step_size > 0, f"Step size must be of type {int} and 1 or higher."
        self.population_size = population_size
        self.population = Population()
        self.database = database
        self.evolver = evolver
        self.hyper_parameters = hyper_parameters
        self.training_service = TrainingService(trainer=trainer, evaluator=evaluator, devices=devices, n_jobs=n_jobs, verbose=max(verbose - 2, 0))
        self.garbage_collector = GarbageCollector(database=database, history_limit=history_limit if history_limit and history_limit > 2 else 2, verbose=verbose-2)
        self.step_size = step_size
        self.loss_metric = loss_metric
        self.eval_metric = eval_metric
        self.loss_functions = loss_functions
        self.end_criteria = end_criteria
        self.detect_NaN = detect_NaN
        self.verbose = verbose
        self.logging = logging
        self.nfe = 0
        self._tensorboard_writer = tensorboard_writer

    def __create_message(self, message : str, tag : str = None) -> str:
        time = get_datetime_string()
        generation = f"G{len(self.population.generations):03d}"
        nfe = f"({self.nfe}/{self.end_criteria['nfe']})" if 'nfe' in self.end_criteria and self.end_criteria['nfe'] else self.nfe
        return f"{time} {nfe} {generation}{f' {tag}' if tag else ''}: {message}"

    def __say(self, message : str, member : Checkpoint = None) -> None:
        """Prints the provided controller message in the appropriate syntax if verbosity level is above 0."""
        self.__register(message=message, verbosity=0, member=member)

    def _whisper(self, message : str, member = None) -> None:
        """Prints the provided controller message in the appropriate syntax if verbosity level is above 1."""
        self.__register(message=message, verbosity=1, member=member)

    def __register(self, message : str, verbosity : int, member : Checkpoint = None) -> None:
        """Logs and prints the provided message in the appropriate syntax. If a member is provided, the message is attached to that member."""
        print_tag = member if member else self.__class__.__name__
        file_tag = f"member_{member.id}" if member else self.__class__.__name__
        full_message = self.__create_message(message, tag=print_tag)
        self.__print(message=full_message, verbosity=verbosity)
        self.__log_to_file(message=full_message, tag=file_tag)
        self.__log_to_tensorboard(message=full_message, tag=file_tag, global_steps=member.steps if member else None)
    
    def __print(self, message : str, verbosity : int) -> None:
        if self.verbose > verbosity:
            print(message)

    def __log_to_file(self, message : str, tag : str) -> None:
        if not self.logging:
            return
        with self.database.create_file(tag='logs', file_name=f"{tag}_log.txt").open('a+') as file:
            file.write(message + '\n')
    
    def __log_to_tensorboard(self, message : str, tag : str, global_steps : int = None) -> None:
        if not self._tensorboard_writer:
            return
        self._tensorboard_writer.add_text(tag=tag, text_string=message, global_step=global_steps)

    def __update_tensorboard(self, member : Checkpoint) -> None:
        """Plots member data to tensorboard"""
        if not self._tensorboard_writer:
            return       
        # plot eval metrics
        for eval_metric_group, eval_metrics in member.loss.items():
            for metric_name, metric_value in eval_metrics.items():
                self._tensorboard_writer.add_scalar(
                    tag=f"metrics/{eval_metric_group}_{metric_name}/{member.id:03d}",
                    scalar_value=metric_value,
                    global_step=member.steps)
        # plot time
        for time_type, time_value in member.time.items():
            self._tensorboard_writer.add_scalar(
                tag=f"time/{time_type}/{member.id:03d}",
                scalar_value=time_value,
                global_step=member.steps)
        # plot hyper-parameters
        for hparam_name, hparam in member.parameters.items():
            self._tensorboard_writer.add_scalar(
                tag=f"hyperparameters/{hparam_name}/{member.id:03d}",
                scalar_value=hparam.normalized if isinstance(hparam, DiscreteHyperparameter) else hparam.value,
                global_step=member.steps)

    def __create_member(self, id) -> Checkpoint:
        """Create a member object"""
        # create new member object
        member = Checkpoint(
            id=id,
            parameters=copy.deepcopy(self.hyper_parameters),
            loss_metric=self.loss_metric,
            eval_metric=self.eval_metric,
            minimize=self.loss_functions[self.eval_metric].minimize)
        # process new member with evolver
        self.evolver.on_spawn(member, self._whisper)
        return member

    def __create_members(self, k : int) -> List[Checkpoint]:
        members = list()
        for id in range(k):
            members.append(self.__create_member(id))
        return members

    def __update_database(self, member : Checkpoint) -> None:
        """Updates the database stored in files."""
        self._whisper(f"updating member {member.id} in database...")
        self.database.update(member.id, member.steps, member)

    def __is_member_finished(self, member : Checkpoint) -> bool:
        """With the end_criteria, check if the provided member is finished training."""
        if 'steps' in self.end_criteria and self.end_criteria['steps'] and member.steps >= self.end_criteria['steps']:
            # the number of steps is equal or above the given treshold
            return True
        return False
    
    def __is_population_finished(self) -> bool:
        """
        With the end_criteria, check if the entire population is finished
        by inspecting the provided member.
        """
        if 'nfe' in self.end_criteria and self.end_criteria['nfe'] and self.nfe >= self.end_criteria['nfe']:
            return True
        if 'score' in self.end_criteria and self.end_criteria['score'] and any(member >= self.end_criteria['score'] for member in self.population.current):
            return True
        if all(self.__is_member_finished(member) for member in self.population.current):
            return True
        return False

    def __create_initial_generation(self) -> Generation:
        new_members = self.__create_members(k=self.population_size)
        generation = Generation()
        for member in self.training_service.train(new_members, self.step_size, None, False, False):
            # log performance
            self.__say(member.performance_details(), member)
            # Save member to database directory.
            self.__update_database(member)
            generation.append(member)
        return generation

    def start(self, use_old = False) -> None:
        """
        Start global training procedure. Ends when end_criteria is met.
        """
        try:
            self.__on_start()
            # start controller loop
            self.__say("Starting training procedure...")
            if not use_old:
                self.__train_synchronously()
            else:
                self.__train_synchronously_old()
            # terminate worker processes
            self.__say("finished.")
        except KeyboardInterrupt:
            self.__say("interupted.")
        finally:
            self.__on_end()

    def __on_start(self) -> None:
        """Resets class properties, starts training service and cleans up temporary files."""
        # reset class properties
        self.nfe = 0
        self.generations = 0
        self.population = Population()
        # start training service
        self.training_service.start()

    def __on_end(self) -> None:
        """Stops training service and cleans up temporary files."""
        # close training service
        self.training_service.stop()

    def __train_synchronously(self) -> None:
        """
        Performs the training of the population synchronously.
        Each member is trained individually and asynchronously,
        but they are waiting for each other between each generation cycle.
        """
        self.__say("Training initial generation...")
        self.population.append(self.__create_initial_generation())
        while not self.__is_population_finished():
            self._whisper("on generation start")
            self.evolver.on_generation_start(self.population.current, self._whisper)
            # create new generation
            new_generation = Generation()
            # 1. evolve, 2. train, 3. evaluate, 4. save
            # generate new candidates
            new_candidates = list(self.evolver.on_evolve(self.population.current, self._whisper))
            # train new candidates
            for candidates in self.training_service.train(new_candidates, self.step_size, None, False, False):
                member = self.evolver.on_evaluate(candidates, self._whisper)
                self.nfe += 1 #if isinstance(candidates, Checkpoint) else len(candidates)
                # log performance
                self.__say(member.performance_details(), member)
                # Save member to database directory.
                self.__update_database(member)
                # write to tensorboard if enabled
                self.__update_tensorboard(member)
                # Add member to generation.
                new_generation.append(member)
                self._whisper("awaiting next trained member...")
            self._whisper("on generation end")
            self.evolver.on_generation_end(new_generation, self._whisper)
            # add new generation
            self.population.append(new_generation)
            # perform garbage collection
            self._whisper("performing garbage collection...")
            self.garbage_collector.collect(self.population.generations)
        self.__say(f"end criteria has been reached.")

    def __train_synchronously_old(self, eval_steps=8) -> None:
        """
        Performs the training of the population synchronously.
        Each member is trained individually and asynchronously,
        but they are waiting for each other between each generation cycle.
        """
        self.__say("Training initial generation...")
        self.population.append(self.__create_initial_generation())
        while not self.__is_population_finished():
            self._whisper("on generation start")
            self.evolver.on_generation_start(self.population.current, self._whisper)
            # create new generation
            new_generation = Generation()
            # generate new candidates
            new_candidates = list(self.evolver.on_evolve(self.population.current, self._whisper))
            best_candidates = list()
            # test candidates with a smaller eval step
            for candidates in self.training_service.train(new_candidates, eval_steps, eval_steps, False, True):
                member = self.evolver.on_evaluate(candidates, self._whisper)
                best_candidates.append(member)
                self.nfe += 1
            # train best candidate on full dataset
            for member in self.training_service.train(best_candidates, self.step_size - eval_steps, None, False, False):
                # log performance
                self.__say(member.performance_details(), member)
                # Save member to database directory.
                self.__update_database(member)
                # write to tensorboard if enabled
                self.__update_tensorboard(member)
                # Add member to generation.
                new_generation.append(member)
                self._whisper("awaiting next trained member...")
            self._whisper("on generation end")
            self.evolver.on_generation_end(new_generation, self._whisper)
            # add new generation
            self.population.append(new_generation)
            # perform garbage collection
            self._whisper("performing garbage collection...")
            self.garbage_collector.collect(self.population.generations)
        self.__say(f"end criteria has been reached.")