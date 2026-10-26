"""Record exception locations without exception messages, source text or locals."""
from pathlib import Path
import traceback


def failure_context(error: BaseException,project: Path) -> dict:
    frames=[]
    for frame in traceback.extract_tb(error.__traceback__):
        path=Path(frame.filename).resolve()
        try: name=str(path.relative_to(project.resolve()))
        except ValueError: name=path.name
        frames.append({"file":name,"function":frame.name,"line":frame.lineno})
    return {"type":type(error).__name__,"frames":frames}
