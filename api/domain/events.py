from typing import Any, Mapping, get_type_hints

class Event:
  map_id: str
  map_version: str
  event_name: str
  params: object

  def __init__(self, props: Mapping[str, Any]):
    for name, expected_type in get_type_hints(type(self)).items():
      if name not in props:
        raise ValueError(f'Parâmetro obrigatório "{name}" ausente.')

      value = props[name]
      if expected_type is not object and not isinstance(value, expected_type):
        raise TypeError(
          f'Parâmetro "{name}" deve ser do tipo {expected_type.__name__}.'
        )

      setattr(self, name, value)

evt = Event({'map_id':123})
