"""Skills layer — business logic plugins.

导入所有 Skill 模块以触发 @register_skill 装饰器注册。
"""

from . import (
    code_review,  # noqa: F401
    ddl_generate,  # noqa: F401
    design_scheme,  # noqa: F401
    dqc_quality,  # noqa: F401
    report_delivery,  # noqa: F401
    requirement_clarify,  # noqa: F401
    sql_develop,  # noqa: F401
)
