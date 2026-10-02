from typing import Any, Mapping, get_args, get_origin, get_type_hints
from events import Event

class Map:
  map_id: str
  map_version: str
  events: list[Event]

  def __init__(self, props: Mapping[str, Any]):
    for name, expected_type in get_type_hints(type(self)).items():
      if name not in props:
        raise ValueError(f'Parâmetro obrigatório "{name}" ausente.')

      value = props[name]
      if get_origin(expected_type) is list:
        item_type = get_args(expected_type)[0]
        is_valid = isinstance(value, list) and all(
          isinstance(item, item_type) for item in value
        )
      else:
        is_valid = expected_type is object or isinstance(value, expected_type)

      if not is_valid:
        raise TypeError(
          f'Parâmetro "{name}" deve ser do tipo '
          f'{getattr(expected_type, "__name__", expected_type)}.'
        )

      setattr(self, name, value)

map = Map({'map_id':"abc"})
