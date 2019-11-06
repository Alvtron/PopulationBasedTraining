import random
from dataclasses import dataclass

def hyperparameters_to_value_dict(hyperparameter_dict : dict):
    value_dict = {}
    for parameter_name, parameter_object in hyperparameter_dict.items():
        value_dict[parameter_name] = parameter_object._value
    return value_dict

def translate(value, left_min, left_max, right_min, right_max):
    # Calculate the span of each range
    left_span = left_max - left_min
    right_span = right_max - right_min
    # normalize the value from the left range into a float between 0 and 1
    value_normalized = float(value - left_min) / float(left_span)
    # Convert the normalize value range into a value in the right range.
    return right_min + (value_normalized * right_span)

def clip(value, min_value, max_value):
    if value < min_value:
        return min_value
    elif value > max_value:
        return max_value
    else:
        return value

class Hyperparameter(object):
    ''' Class for creating and storing a hyper-parameter in a given, constrained search space. '''
    def __init__(self, *args, is_categorical = False):
        ''' Provide a set of [lower bound, upper bound] as float/int, or categorical elements [obj1, obj2, ..., objn]. Make sure to set is_categorical = True if categorical values are provided. Sets the search space and sorts it, then samples a new candidate from an uniform distribution. '''
        assert args and len(list(args)) > 1, "Hyperparameter initialization needs at least two arguments."
        assert not is_categorical and isinstance(args[0], (float, int)), "Non-categorical hyperparameters must be of type float or int."
        self._search_space = sorted(list(args))
        self.is_categorical = is_categorical
        self._value = None

    def _get_normalized_value(self, value):
        """Returns a normalized version of the provided hyperparameter value."""
        if self.is_categorical:
            assert value in self._search_space, "The provided value does not exist within the categorical search space."
            index = self._search_space.index(value)
            return translate(index, 0, len(self._search_space) - 1, 0.0, 1.0)
        elif isinstance(value, (int, float)):
            return translate(value, self.get_lower_bound(), self.get_upper_bound(), 0.0, 1.0)
        else:
            raise Exception("Non-categorical values must be of type float or int.")

    def get_normalized(self):
        """Returns the normalized hyperparameter value."""
        return self._value

    def get_value(self):
        """Returns the representative hyperparameter value."""
        if self.is_categorical:
            index = int(round(translate(self._value, 0.0, 1.0, 0, len(self._search_space) - 1)))
            return self._search_space[index]
        elif isinstance(self._search_space[0], float):
            return float(translate(self._value, 0.0, 1.0, self.get_lower_bound(), self.get_upper_bound()))
        elif isinstance(self._search_space[0], int):
            return int(round(translate(self._value, 0.0, 1.0, self.get_lower_bound(), self.get_upper_bound())))
        else:
            raise Exception("Non-categorical hyperparameters must be of type float or int.")

    def set_value(self, value):
        """Sets the normalized hyperparameter value."""
        self._value = clip(self._get_normalized_value(value), 0.0, 1.0)

    def get_lower_bound(self):
        ''' Returns the lower bounds of the hyper-parameter search space. '''
        return self._search_space[0]

    def get_upper_bound(self):
        ''' Returns the upper bounds of the hyper-parameter search space. '''
        return self._search_space[-1]

    def sample_uniform(self):
        ''' Samples a new candidate from an uniform distribution bound by the lower and upper bounds. '''
        self._value = random.uniform(0.0, 1.0)
        return self.get_value()

    def update(self, expression):
        ''' Changes the hyper-parameter value with the given expression. '''
        self._value = float(clip(expression(self._value), 0.0, 1.0))
        return self.get_value()

    def __str__(self):
        return f"{self._value}, U({self.get_lower_bound()},{self.get_upper_bound()})"

    def __add__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            return clip(other + self._value, 0.0, 1.0) 
        else:
            raise Exception("Addition is supported for values of type Hyperparameter, float or int.")

    def __sub__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            return clip(other - self._value, 0.0, 1.0)
        else:
            raise Exception("Subtraction is supported for values of type Hyperparameter, float or int.")

    def __mul__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            return clip(other * self._value, 0.0, 1.0)
        else:
            raise Exception("Multiplication is supported for values of type Hyperparameter, float or int.")

    def __div__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            return clip(other / self._value, 0.0, 1.0)
        else:
            raise Exception("Divition is supported for values of type Hyperparameter, float or int.")

    def __pow__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            return clip(other**self._value, 0.0, 1.0)
        else:
            raise Exception("Power is supported for values of type Hyperparameter, float or int.")

    def __iadd__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            self._value = self._value + other
            return self
        else:
            raise Exception("Addition is supported for values of type Hyperparameter, float or int.")

    def __isub__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            self._value = self._value - other
            return self
        else:
            raise Exception("Subtraction is supported for values of type Hyperparameter, float or int.")

    def __imul__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            self._value = self._value * other
            return self
        else:
            raise Exception("Multiplication is supported for values of type Hyperparameter, float or int.")

    def __idiv__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            self._value = self._value / other
            return self
        else:
            raise Exception("Divition is supported for values of type Hyperparameter, float or int.")

    def __ipow__(self, other):
        if isinstance(other, (Hyperparameter, float, int)):
            self._value = self._value ** other
            return self
        else:
            raise Exception("Power is supported for values of type Hyperparameter, float or int.")