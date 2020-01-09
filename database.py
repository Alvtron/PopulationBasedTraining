import os
import pickle
from pathlib import Path
from datetime import datetime

class Checkpoint(object):
    '''Class for keeping track of a worker.'''
    def __init__(self, id, hyper_parameters, loss_metric, eval_metric):
        self.id = id
        self.epochs = 0
        self.steps = 0
        self.hyper_parameters = hyper_parameters
        self.model_state = None
        self.optimizer_state = None
        self.loss_metric = loss_metric
        self.eval_metric = eval_metric
        self.loss = dict()
        self.time = dict()

    @property
    def score(self):
        return self.loss['eval'][self.eval_metric]

    def __str__(self):
        string = f"Member {self.id:03d}, epoch {self.epochs}, step {self.steps}"
        for loss_group, loss_values in self.loss.items():
            for loss_name, loss_value in loss_values.items():
                string += f", {loss_group}_{loss_name} {loss_value:.5f}"
        return string

    def update(self, checkpoint):
        self.hyper_parameters = checkpoint.hyper_parameters
        self.model_state = checkpoint.model_state
        self.optimizer_state = checkpoint.optimizer_state
        self.loss = dict()
        self.time = dict()

class ReadOnlyDatabase(object):
    def __init__(self, database_path, read_function=None):
        self.ENTRIES_TAG = 'entries'
        self.path = database_path
        # set read function
        def read(path): return pickle.load(path.open('rb'))
        self.read = read if not read_function else read_function

    @property
    def exists(self):
        return self.path.is_dir

    def create_entry_directoy_path(self, id):
        """ Creates a new entry directory file path. """
        return Path(self.path, self.ENTRIES_TAG, f"{id:03d}")

    def create_entry_file_path(self, id, steps):
        """ Creates and returns a new database entry file path in the appropriate entry directory. """
        entry_directory = self.create_entry_directoy_path(id)
        return Path(entry_directory, f"{steps:05d}.pth")

    def get_entry(self, id, steps):
        """ Returns the specific entry stored on the specified id. If there is no match, None is returned. """
        entry_file_path = self.create_entry_file_path(id, steps)
        if not entry_file_path.is_file():
            return None
        entry = self.read(entry_file_path)
        return entry

    def get_entry_directories(self):
        entries_path = Path(self.path, self.ENTRIES_TAG)
        return [content for content in entries_path.iterdir() if content.is_dir()]
            
    def get_entries(self, entry_directory):
        """ Retrieve all entries made on the specified id. """
        return [self.read(content) for content in entry_directory.iterdir()]

    def to_dict(self):
        """ Returns a the database converted to a dictionary grouped by id/filename/entry"""
        dict_of_entries = dict()
        for directory in self.get_entry_directories():
            entries = self.get_entries(directory)
            if not directory.name in dict_of_entries:
                dict_of_entries[directory.name] = dict()
            for entry in entries:
                dict_of_entries[directory.name][entry.steps] = entry
        return dict_of_entries
        
    def to_list(self):
        """ Returns a the database converted to a list of all entries """
        list_of_entries = []
        for directory in self.get_entry_directories():
            entries = self.get_entries(directory)
            list_of_entries.extend(entries)
        return list_of_entries

    def print(self):
        """ Prints all entries in the database. """
        database_list = self.to_list()
        database_list_sorted = sorted(database_list, key=lambda x: (x.id, x.steps), reverse=False)
        for entry in database_list_sorted:
            print(entry)

class Database(ReadOnlyDatabase):
    def __init__(self, directory_path, database_name=None, read_function=None, write_function=None):
        # create database path
        self.DATE_CREATED = datetime.now()
        database_name = self.DATE_CREATED.strftime('%Y%m%d%H%M%S') if not database_name else database_name
        # create parent object
        super().__init__(database_path=Path(directory_path, database_name), read_function=read_function)
        # create database directory
        self.path.mkdir(parents=True, exist_ok=True)
        # set write function
        def write(entry, path): pickle.dump(entry, path.open('wb'))
        self.write = write if not write_function else write_function

    def create_folder(self, name):
        """ Create a new folder located in the database base directory. Name supports nested directories of type dir/sub_dir/sub_sub_dir/etc. """
        folder_path = Path(self.path, name)
        folder_path.mkdir(parents=True, exist_ok=True)
        return folder_path

    def create_file(self, tag, file_name):
        """ Create a new file in a folder named after the specified tag-string, which is located in the database directory. """
        file_path = Path(self.path, tag, file_name)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def save_entry(self, entry):
        """ Save the provided database entry to a file inside the database directory. """
        entry_file_path = self.create_entry_file_path(entry.id, entry.steps)
        entry_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.write(entry, entry_file_path)

class SharedDatabase(Database):
    def __init__(self, context, directory_path, database_name=None, read_function=None, write_function=None):
        super().__init__(
            directory_path=directory_path,
            database_name=database_name,
            read_function=read_function,
            write_function=write_function)
        manager = context.Manager() 
        self.cache = manager.dict()

    def save_entry_to_file(self, entry):
        """ Save the provided database entry to a file inside the database directory. """
        entry_file_path = self.create_entry_file_path(entry.id, entry.steps)
        entry_file_path.parent.mkdir(parents=True, exist_ok=True)
        self.write(entry, entry_file_path)

    def save_entry(self, entry):
        """ Saves entry to memory, and will replace any old entry. In addition, the method saves the provided entry to the database directory. """
        # Save entry to cache memory. This replaces the old entry.
        self.cache[entry.id] = entry
        # Save entry to database directory.
        super().save_entry(entry)

    def get_entry(self, id, steps=None):
        """ Returns the specific entry stored on the specified id. If there is no match, None is returned. """
        if steps == None:
            return self.cache[id] if self.cache[id] else None
        return super().get_entry(id, steps)

    def get_latest(self):
        """ Returns a list containing the latest entry from every member. """
        return self.cache.values()