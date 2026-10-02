import re

actual_value = ""
issues = []
expected_params = {"page":"%"}

for key, value in expected_params.items():
  expected_value = expected_params.get(key)
  # Só valida se o valor esperado não for None/vazio.
  if expected_value is not None and expected_value != "":
    if "%" in expected_value:
        pattern = ".+".join(
            re.escape(part) for part in str(expected_value).split("%")
        )
        print('pattern',pattern)
        matches = re.fullmatch(pattern, str(actual_value)) is not None
    else:
        matches = actual_value == expected_value

    if not matches:
        issues.append(f"Valor de '{key}' inválido. Esperado: '{expected_value}', recebido: '{actual_value}'")

print('matches',matches)
print('issues', issues)