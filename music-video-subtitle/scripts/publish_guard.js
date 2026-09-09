/* Browser-agnostic guard; callbacks must use the selected browser's documented API. */
function createPublishGuard({ readFields, now = () => Date.now(), budgetMs = 60000 } = {}) {
  if (typeof readFields !== "function") throw new Error("必须提供一次性只读字段快照");
  let busy = false;
  let blocked = false;
  const records = [];
  const fields = new Map();
  const same = (a, b) => String(a).replace(/\r\n/g, "\n") === String(b).replace(/\r\n/g, "\n");

  async function setField({ name, expected, write, settle, repair }) {
    if (blocked) throw new Error("此页面已停止，先解决之前的失败");
    if (busy) throw new Error("禁止并发写入共享焦点");
    if (!name || typeof write !== "function" || typeof settle !== "function") {
      throw new Error("必须提供明确字段及写入、失焦回调");
    }
    busy = true;
    const started = now();
    const entry = { name, status: "pending", attempts: 0, elapsedMs: 0 };
    records.push(entry);
    const withinBudget = () => {
      if (now() - started > budgetMs) throw new Error("字段耗时超出预算，停止追加调用");
    };
    try {
      for (const action of [write, repair].filter(Boolean)) {
        withinBudget();
        entry.attempts += 1;
        await action(expected);
        withinBudget();
        await settle();
        withinBudget();
        const snapshot = await readFields([...new Set([...fields.keys(), name])]);
        withinBudget();
        if (Object.hasOwn(snapshot, name) && same(snapshot[name], expected)) {
          // A new field may cause a framework rerender that reverts an older field.
          for (const [priorName, prior] of fields) {
            if (priorName === name) continue;
            if (!Object.hasOwn(snapshot, priorName) || !same(snapshot[priorName], prior)) {
              throw new Error("后续操作导致已确认字段回退：" + priorName);
            }
          }
          fields.set(name, expected);
          entry.status = "verified";
          return { ...entry, elapsedMs: now() - started };
        }
      }
      throw new Error("字段失焦后未保留：" + name);
    } catch (error) {
      blocked = true;
      entry.status = "blocked";
      // Do not replay an operation whose completion is uncertain after an exception.
      throw error;
    } finally {
      entry.elapsedMs = now() - started;
      busy = false;
    }
  }

  return { setField, report: () => records.map(row => ({ ...row })) };
}

if (typeof module !== "undefined") module.exports = { createPublishGuard };
