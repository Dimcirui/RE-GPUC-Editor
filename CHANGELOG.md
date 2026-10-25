# 版本记录

## 未发布

- 修复不选网格集合导入（Keep Structure）时参考骨架不生效：胶囊端点和根关节现在按 data/skeletons.json 放在 vanilla 位置，不再堆在原点；胶囊偏移的方向也与有网格时一致。已导入的集合需重新导入。

## 0.3.1

- General Tools：Check All 改名为 **Check**，和 Create（新建 GPUC Header）并排放在第二行，与 Import / Export 上下对应。Check 检查活动 GPUC 的所有 Part。
- 加入 GPL-3.0 许可证全文。安装包只包含插件本体。

## 0.3.0

- 支持 Onimusha: Way of the Sword（`.gpuc.260209505`）：导入（Keep Structure、Rebuild from Bones）、导出、新建布料。21 个原版文件中，18 个无改动导出逐字节一致；另外 3 个直接驱动顶点、没有 Mesh Multi Joint Deform，按与 Wilds 相同的规则拒绝导入。
- 鬼武者布料导出时保留自带胶囊（RE9 原版没有胶囊段，导出时丢弃并提示）。
- Part 的 Friction 取消上限 1：鬼武者原版有 30 和 100。滑块仍到 1，预览模拟按 1 截断。

## 0.2.0

- Mapping Range 的行为改变：修改 Min / Max 时保留顶点组权重和颜色分布，改为更新实际参数值（碰撞半径、限制距离等随之变化）。0.1.x 的做法相反。

## 0.1.10

- 修复界面着色问题。

## 0.1.9

- 修复粒子着色与当前编辑参数的同步：修改 Value、Mapping Range，或执行 Paint / Set on Selected 时，Display 的粒子着色切换到当前参数。

## 0.1.8

- 调整粒子参数的权重映射。

## 0.1.7

- 调整粒子布局。

## 0.1.6

- 修复安装、启用时访问 Blender 受限上下文导致的 `_RestrictContext.active_object` 错误。初始选择同步改到注册完成后执行，禁用时清理尚未执行的定时回调。

## 0.1.5

- 新增自定义参数预设（GPUC: Presets）。

## 0.1.3 – 0.1.4

- 新增粒子位置偏移：在编辑模式里移动生成的粒子，退出时保存。

## 0.1.2

- RE4 试验支持：修正碰撞掩码偏移为 Part `+0x34 / +0x38 / +0x3C`；`+0x30` 单独作为 float 编辑。旧试验版导入的 RE4 集合需要重新导入。

## 0.1.1

- 首个打包版本；另有 RE4 Keep Structure 的试验版。
