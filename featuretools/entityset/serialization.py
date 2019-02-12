import json
import os
import shutil

import pandas as pd

from .. import variable_types
from .relationship import Relationship

FORMATS = ['csv', 'pickle', 'parquet']
VARIABLE_TYPES = {
    getattr(variable_types, type).type_string: getattr(variable_types, type) for type in dir(variable_types)
    if hasattr(getattr(variable_types, type), 'type_string')
}


def to_entity_description(entity):
    '''Serialize entity to data description.

    Args:
        entity (Entity) : Instance of :class:`.Entity`.

    Returns:
        dictionary (dict) : Description of :class:`.Entity`.
    '''
    index = entity.df.columns.isin([variable.id for variable in entity.variables])
    dtypes = entity.df[entity.df.columns[index]].dtypes.astype(str).to_dict()
    description = {
        "id": entity.id,
        "index": entity.index,
        "time_index": entity.time_index,
        "properties": {
            'secondary_time_index': entity.secondary_time_index,
            'last_time_index': entity.last_time_index is not None,
        },
        "variables": [variable.to_data_description() for variable in entity.variables],
        "loading_info": {
            'params': {},
            'properties': {
                'dtypes': dtypes
            }
        }
    }

    return description


def to_relationship_description(relationship):
    '''Serialize entityset relationship to data description.

    Args:
        relationship (Relationship) : Instance of :class:`.Relationship`.

    Returns:
        description (dict) : Description of :class:`.Relationship`.
    '''
    return {
        'parent': [relationship.parent_entity.id, relationship.parent_variable.id],
        'child': [relationship.child_entity.id, relationship.child_variable.id],
    }


def write_entity_data(entity, path, format='csv', **kwargs):
    '''Write entity data to disk.

    Args:
        entity (Entity) : Instance of :class:`.Entity`.
        path (str) : Location on disk to write entity data.
        format (str) : Format to use for writing entity data. Defaults to csv.
        kwargs (keywords) : Additional keyword arguments to pass as keywords arguments to the underlying serialization method.

    Returns:
        loading_info (dict) : Information on storage location and format of entity data.
    '''
    format = format.lower()
    basename = '.'.join([entity.id, format])
    if 'compression' in kwargs:
        basename += '.' + kwargs['compression']
    location = os.path.join('data', basename)
    file = os.path.join(path, location)
    if format == 'csv':
        params = ['index', 'encoding']
        subset = {key: kwargs[key] for key in params if key in kwargs}
        entity.df.to_csv(file, **subset)
    elif format == 'parquet':
        # Serializing to parquet format raises an error when columns contain tuples.
        # Columns containing tuples are mapped as dtype object.
        # Issue is resolved by casting columns of dtype object to string.
        columns = entity.df.select_dtypes('object').columns
        entity.df[columns] = entity.df[columns].astype('unicode')
        entity.df.columns = entity.df.columns.astype('unicode')  # ensures string column names for python 2.7
        entity.df.to_parquet(file, **kwargs)
    elif format == 'pickle':
        entity.df.to_pickle(file, **kwargs)
    else:
        error = 'must be one of the following formats: {}'
        raise ValueError(error.format(', '.join(FORMATS)))
    return {'location': location, 'type': format, 'params': kwargs}


def write_data_description(entityset, path, **kwargs):
    '''Serialize entityset to data description and write to disk.

    Args:
        entityset (EntitySet) : Instance of :class:`.EntitySet`.
        path (str) : Location on disk to write `data_description.json` and entity data.
        kwargs (keywords) : Additional keyword arguments to pass as keywords arguments to the underlying serialization method.
    '''
    path = os.path.abspath(path)
    if os.path.exists(path):
        shutil.rmtree(path)
    for dirname in [path, os.path.join(path, 'data')]:
        os.makedirs(dirname)
    description = entityset.to_data_description()
    for entity in entityset.entities:
        loading_info = write_entity_data(entity, path, **kwargs)
        description['entities'][entity.id]['loading_info'].update(loading_info)
    file = os.path.join(path, 'data_description.json')
    with open(file, 'w') as file:
        json.dump(description, file)


def from_variable_description(description, entity=None):
    '''Deserialize variable from variable description.

    Args:
        description (dict) : Description of :class:`.Variable`.
        entity (Entity) : Instance of :class:`.Entity` to add :class:`.Variable`. If entity is None, :class:`.Variable` will not be instantiated.

    Returns:
        variable (Variable) : Returns :class:`.Variable`.
    '''
    is_type_string = isinstance(description['type'], str)
    type = description['type'] if is_type_string else description['type'].pop('value')
    variable = VARIABLE_TYPES.get(type, VARIABLE_TYPES.get(None))
    if entity is not None:
        kwargs = {} if is_type_string else description['type']
        variable = variable(description['id'], entity, **kwargs)
        variable.interesting_values = description['properties']['interesting_values']
    return variable


def from_entity_description(description, entityset, path=None):
    '''Deserialize entity from entity description and add to entityset.

    Args:
        description (dict) : Description of :class:`.Entity`.
        entityset (EntitySet) : Instance of :class:`.EntitySet` to add :class:`.Entity`.
        path (str) : Root directory to serialized entityset.
    '''
    from_disk = path is not None
    dataframe = read_entity_data(description, path=path) if from_disk else empty_dataframe(description)
    variable_types = {variable['id']: from_variable_description(variable) for variable in description['variables']}
    entityset.entity_from_dataframe(
        description['id'],
        dataframe,
        index=description.get('index'),
        time_index=description.get('time_index'),
        secondary_time_index=description['properties'].get('secondary_time_index'),
        variable_types=variable_types)


def from_relationship_description(description, entityset):
    '''Deserialize parent and child variables from relationship description.

    Args:
        description (dict) : Description of :class:`.Relationship`.
        entityset (EntitySet) : Instance of :class:`.EntitySet` containing parent and child variables.

    Returns:
        item (tuple(Variable, Variable)) : Tuple containing parent and child variables.
    '''
    entity, variable = description['parent']
    parent = entityset[entity][variable]
    entity, variable = description['child']
    child = entityset[entity][variable]
    return Relationship(parent, child)


def empty_dataframe(description):
    '''Deserialize empty dataframe from entity description.

    Args:
        description (dict) : Description of :class:`.Entity`.

    Returns:
        df (DataFrame) : Empty dataframe for entity.
    '''
    columns = [variable['id'] for variable in description['variables']]
    dtypes = description['loading_info']['properties']['dtypes']
    return pd.DataFrame(columns=columns).astype(dtypes)


def read_entity_data(description, path=None):
    '''Read description data from disk.

    Args:
        description (dict) : Description of :class:`.Entity`.

    Returns:
        df (DataFrame) : Instance of dataframe.
    '''
    file = os.path.join(path, description['loading_info']['location'])
    kwargs = description['loading_info'].get('params', {})
    if description['loading_info']['type'] == 'csv':
        params = ['compression', 'engine', 'encoding']
        subset = {key: kwargs[key] for key in params if key in kwargs}
        dataframe = pd.read_csv(file, **subset)
    elif description['loading_info']['type'] == 'parquet':
        params = ['engine', 'columns']
        subset = {key: kwargs[key] for key in params if key in kwargs}
        dataframe = pd.read_parquet(file, **subset)
    elif description['loading_info']['type'] == 'pickle':
        params = ['compression']
        subset = {key: kwargs[key] for key in params if key in kwargs}
        dataframe = pd.read_pickle(file, **subset)
    else:
        error = 'must be one of the following formats: {}'
        raise ValueError(error.format(', '.join(FORMATS)))
    dtypes = description['loading_info']['properties']['dtypes']
    return dataframe.astype(dtypes)


def read_data_description(path):
    '''Read data description from disk.

        Args:
            path (str): Location on disk to read `data_description.json`.

        Returns:
            description (dict) : Description of :class:`.EntitySet`.
    '''
    path = os.path.abspath(path)
    assert os.path.exists(path), '"{}" does not exist'.format(path)
    file = os.path.join(path, 'data_description.json')
    with open(file, 'r') as file:
        description = json.load(file)
    description['root'] = path
    return description
