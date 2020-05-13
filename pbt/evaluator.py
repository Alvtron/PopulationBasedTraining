import time
from copy import deepcopy

import torch
from torch.utils.data import DataLoader
from torch.utils.data import Dataset, DataLoader
from torch.nn import Module

from .hyperparameters import Hyperparameters
from .utils.data import create_subset, create_subset_with_indices

class Evaluator(object):
    """ Class for evaluating the performance of the provided model on the set evaluation dataset. """
    def __init__(self, model_class : Module, test_data : Dataset, batch_size : int, loss_functions : dict,
            loss_group : str = 'eval', verbose : bool = False):
        self.model_class = model_class
        self.test_data = test_data
        self.batch_size = batch_size
        self.loss_functions = loss_functions
        self.loss_group = loss_group
        self.verbose = verbose

    def _print(self, message : str = None, end : str = '\n'):
        if not self.verbose:
            return
        print(message, end=end)

    def create_model(self, model_state = None, device : str = 'cpu'):
        self._print("creating model...")
        model = self.model_class().to(device)
        if model_state:
            model.load_state_dict(model_state)
        model.eval()
        return model

    def __call__(self, checkpoint : dict, step_size : int = None, device : str = 'cpu', shuffle : bool = False):
        """Evaluate model on the provided validation or test set."""
        start_eval_time_ns = time.time_ns()
        self._print("creating model...")
        model = self.create_model(checkpoint.model_state, device)
        self._print("creating batches...")
        batches = DataLoader(dataset = self.test_data, batch_size = self.batch_size, shuffle = shuffle)
        num_batches = len(batches)
        # reset loss dict
        checkpoint.loss[self.loss_group] = dict.fromkeys(self.loss_functions, 0.0)
        self._print("evaluating...")
        for batch_index, (x, y) in enumerate(batches):
            if self.verbose: print(f"({batch_index + 1}/{num_batches})", end=" ")
            x = x.to(device, non_blocking=True)
            y = y.to(device, non_blocking=True)
            with torch.no_grad():
                output = model(x)
            for metric_type, metric_function in self.loss_functions.items():
                with torch.no_grad():
                    loss = metric_function(output, y)
                checkpoint.loss[self.loss_group][metric_type] += loss.item() / float(num_batches)
                if self.verbose: print(f"{metric_type}: {loss.item():4f}", end=" ")
                del loss
            if self.verbose: print(end="\n")
            del output
            if step_size is not None and batch_index + 1 == step_size:
                break
        # clean GPU memory
        del model
        torch.cuda.empty_cache()
        # update checkpoint
        checkpoint.time[self.loss_group] = float(time.time_ns() - start_eval_time_ns) * float(10**(-9))