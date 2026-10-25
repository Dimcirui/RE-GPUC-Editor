# RE GPUC Editor

Blender 插件：为 RE Engine 游戏的 GPU 布料（`.gpuc`）从骨链生成粒子网格，批量设置粒子参数，预览近似模拟，并导入、导出 `.gpuc` 文件。

*A Blender add-on for RE Engine GPU cloth (`.gpuc`): build the particle grid from bone chains, paint and set particle values, preview an approximate simulation, and import / export `.gpuc` files. Supports MH Wilds, RE9 and Onimusha: Way of the Sword; RE4 files can be edited in place. The UI is in English; this README is in Chinese.*

## 支持的游戏

| 游戏 | 扩展名 | 支持范围 | 游戏内验证 |
|---|---|---|---|
| Monster Hunter Wilds | `.gpuc.241111760` | 从骨骼生成、Rebuild from Bones、Keep Structure | 未验证 |
| Resident Evil Requiem（RE9） | `.gpuc.250925365` | 同上 | 未验证 |
| Onimusha: Way of the Sword | `.gpuc.260209505` | 同上 | 未验证 |
| Resident Evil 4 | `.gpuc.221108868` | 仅 Keep Structure（在原文件上改参数） | 未验证 |

“未验证”指导出的文件只通过了格式层面的检查：原版文件无改动导出逐字节一致，编辑后只改动对应字段。尚未在游戏里实际运行过。

## 安装

1. 从 Releases 下载 `ree_gpuc_editor-<版本>.zip`。
2. Blender 4.2 或更新版本：编辑 → 偏好设置 → 获取扩展 → 右上角下拉菜单 → 从磁盘安装，选择这个 zip。
3. 导入、导出 `.gpuc` 需要配合 [RE Mesh Editor](https://github.com/NSACloud/RE-Mesh-Editor) 导入的网格和骨架。

升级时先卸载旧版本。自定义参数预设保存在 Blender 用户脚本目录，不受升级影响。

## 快速上手

1. **新建布料**：侧栏 RE GPUC → GPUC: General Tools → **Create**，在弹窗里选游戏、名字和骨架。
2. **生成 Part**：切到姿态模式，在 GPUC: Pose Mode Tools 里点 Create Part From Parent Bones（只选每条链的首骨）或 Create Part From Selected Bones（选中整条链）。每条骨链是布料的一列。Between bones / Between chains 大于 0 时，会在骨与骨、链与链之间插入粒子。
3. **设置粒子参数**：每个粒子参数对应一个顶点组，如 `gpuc_radius`、`gpuc_max_distance`。可以在 Particles 面板选好参数后点 Paint 直接刷权重；也可以点 Select Particles 选中粒子后，用 Set on Selected 设精确值。权重 0～1 映射到该 Part 的 Mapping Range（Min / Max）。
4. **检查与重新生成**：修改骨骼静止位置并退出骨架编辑模式后，对应 Part 会自动重建。**Check** 检查活动 GPUC 的所有 Part 并报告问题。选中一个可重建的 Part 时，会出现 Regenerate Particles From Part，可以修改闭合和密度。
5. **预览**：GPUC: Preview Simulation → Simulate。重力和风各用一支箭头表示，旋转箭头改方向，缩放改强度。Collider 行可以添加可拖动的球或胶囊。
6. **导出**：General Tools → **Export**。从骨骼生成的布料需要一个同游戏的原版 `.gpuc` 作为模板，用来补上编辑器不生成的数据段。

Config、Part、碰撞体、力的参数都在 Properties → Object 里。

## 导入模式

- **Rebuild from Bones**（默认）：按编辑器的网格模型重建，导入后可以重新生成。需要选择 Mesh Collection。文件结构不符合编辑器模型时，会自动改用 Keep Structure 并给出提示。
- **Keep Structure**：保留原文件的粒子、约束和蒙皮结构，只编辑数值。没有改动时，导出结果和原文件逐字节相同。只能导出为原文件所属的游戏。
- **Update Existing**：把文件里的参数套到已有的 GPUC 集合上。

同一种格式的布料可以通过修改 Header 的 Game 在 Wilds、RE9、鬼武者之间转换，前提是这块布料是从骨骼生成或用 Rebuild from Bones 导入的。导出时要换用目标游戏的模板。

## 移动粒子

选中生成的 Part，按 Tab 进入编辑模式，可以用 `G`、轴向限制或比例编辑移动粒子。退出编辑模式时保存有效位置。

- 偏移保存在骨架的静止空间中。骨骼头位置改变后自动重建，偏移会保留；改变密度时偏移按网格坐标插值。
- 顶点数、连接关系和 UV 受保护：如果删除、合并顶点或改动 UV，退出编辑模式时会恢复到最后一次有效状态。
- **Reset Selected / Reset All** 把粒子恢复到骨骼生成的位置。
- 带偏移的导出文件，再用 Rebuild from Bones 导入时可能会改用 Keep Structure。要继续编辑偏移，请保留 `.blend` 工作文件。
- Keep Structure 的 Part（包括 RE4）不能移动粒子。

## 参数预设

侧栏 **GPUC: Presets** 可以把 Header、Config、Part、Collision、Weld 和预览用的力、碰撞体的参数保存为 JSON 预设，再应用到选中的同类对象上（支持撤销）。

- 预设按游戏分开保存，只能应用到同一游戏的对象。预览用的力和碰撞体在各游戏间共享。
- Part 预设包含物理参数和四种约束形状的参数，不包含逐粒子的值。
- 不保存对象引用、骨骼绑定、根关节、粒子位置偏移、蒙皮和拓扑。
- **Open Preset Folder** 打开预设目录，可以直接复制 JSON 文件分享。

## RE4

RE4 只支持 Keep Structure：导入时自动使用这种模式，不需要 Mesh Collection。

- 可以修改粒子的 Simulation weight、Collision radius、Inverse mass、Max distance、Backstop radius，以及约束参数、Part 的摩擦、反弹、粘性和碰撞掩码，还有 Header 的 Max Velocity。
- Config 里的字段按文件偏移命名（如 `RE4 value +0x24`），含义尚未确认，不能当作 Wilds 的同名参数。
- 名字、Part 和 Config 的数量、骨骼绑定、碰撞体不能修改；不能预览模拟。
- 导出时选 RE4，Joint Indices 用 As Imported，不需要模板。

## 许可证与来源

本插件以 GPL-3.0-or-later 发布，全文见 [LICENSE](LICENSE)。

- `modules/gpuc.py`、`modules/gpuc_build.py` 源自 REE-Modding-Skill 的 `tools/gpuc/`（同为 GPL-3.0），本仓库有改动。
- `data/skeletons.json` 由 MH Wilds 原版 `.fbxskel` 文件生成，只包含骨骼名、名字哈希、父子关系和静止矩阵，用作参考骨架：当胶囊端点或根关节指向的身体骨骼不在衣服网格的骨架里时，用它来定位。

版本记录见 [CHANGELOG.md](CHANGELOG.md)。
