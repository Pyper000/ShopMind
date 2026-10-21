import { useState } from "react";
import type { ProductCard, TradeEvent } from "../types";

function cardGroups(events: TradeEvent[]): { task: string; cards: ProductCard[] }[] {
  let latestPlan = -1;
  for (let index = events.length - 1; index >= 0; index -= 1) {
    if (events[index].type === "plan.update" && events[index].payload?.shopping_plan) {
      latestPlan = index;
      break;
    }
  }
  const relevant = latestPlan >= 0 ? events.slice(latestPlan) : events;
  const groups = new Map<string, ProductCard[]>();
  for (const event of relevant) {
    if (event.type !== "tool.result") continue;
    const cards = event.payload?.hits as ProductCard[] | undefined;
    if (!cards?.length) continue;
    const task = String(event.payload?.shopping_task || "搜索结果");
    const existing = groups.get(task) ?? [];
    for (const card of cards) {
      if (!existing.some((item) => item.product_id === card.product_id)) existing.push(card);
    }
    groups.set(task, existing.slice(0, 5));
  }
  return [...groups].map(([task, cards]) => ({ task, cards }));
}

export default function ProductCards({ events, onCompare, busy }: { events: TradeEvent[]; onCompare: (query: string) => void; busy: boolean }) {
  const groups = cardGroups(events);
  const [selected, setSelected] = useState<string[]>([]);
  const available = new Set(groups.flatMap(group => group.cards.map(card => card.product_id)));
  const compared = selected.filter(id => available.has(id));
  if (!groups.length) return null;

  return (
    <div className="product-groups">
      {compared.length >= 2 && <button type="button" disabled={busy} onClick={() => onCompare(`请比较这些商品：${compared.join("、")}。结合我已说明的目标和条件，解释差异与不足，最后给出首选。`)}>比较已选商品（{compared.length}）</button>}
      {groups.map(({ task, cards }) => (
        <section className="product-group" key={task}>
          <h4>{task}</h4>
          <div className="cards">
            {cards.map((card) => (
        <article key={card.product_id} className="card">
          <label><input type="checkbox" checked={compared.includes(card.product_id)} disabled={busy || (!compared.includes(card.product_id) && compared.length >= 3)} onChange={() => setSelected(compared.includes(card.product_id) ? compared.filter(id => id !== card.product_id) : [...compared, card.product_id])} /> 加入比较</label>
          <header>
            <strong>{card.title}</strong>
            <span className="brand">
              {card.brand} · {card.origin_country}
            </span>
          </header>
          <div className="price">
            {card.price_major} {card.currency}
          </div>
          {card.landed_price && !card.landed_price.unavailable_reason && (
            <div className="landed">
              <div className="landed-total">
                到手价 {card.landed_price.landed_total_major} {card.landed_price.currency}
              </div>
              <div className="landed-detail">
                小计 {card.landed_price.subtotal_major} + 运费 {card.landed_price.freight_major} + 关税{" "}
                {card.landed_price.tariff_major}
                {card.landed_price.de_minimis_applied ? "（免税额度内）" : ""}
              </div>
            </div>
          )}
          <ul className="highlights">
            {card.highlights.map((highlight) => (
              <li key={highlight}>{highlight}</li>
            ))}
          </ul>
          <div className="skus">
            {card.skus.map((sku) => (
              <span key={sku.sku_id} className="sku">
                {sku.spec} · {sku.price_major} {sku.currency} · 库存 {sku.stock}
              </span>
            ))}
          </div>
        </article>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
