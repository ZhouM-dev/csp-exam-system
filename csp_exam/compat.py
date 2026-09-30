"""兼容层：让老的平铺写法在包结构里继续能跑。

历史上这些模块是平铺在部署根目录的（`import store`、`import wrapper`…），
一些运维脚本和自测还这么写。这里把 core/ 里的模块注册成顶层名字，
导入 csp_exam 后 `import store` 依然可用（指向同一个模块对象，不是副本）。
"""

import sys

from .core import hydro_client, importer, problems, store, util, wrapper
from .web import server

for _name, _mod in (("store", store), ("hydro_client", hydro_client), ("wrapper", wrapper),
                    ("make_problem", problems), ("import_problemset", importer),
                    ("util", util), ("exam_server", server)):
    sys.modules.setdefault(_name, _mod)
