from naeural_core.main.ver import __VER__

if __name__ == "__main__":
  with open("pyproject.toml", "rt", encoding="utf-8") as fd:
    new_lines = []
    lines = fd.readlines()
    for line in lines:
      if "version" in line:
        line = f'version = "{__VER__}"\n'
      new_lines.append(line)

  with open("pyproject.toml", "wt", encoding="utf-8") as fd:
    fd.writelines(new_lines)
