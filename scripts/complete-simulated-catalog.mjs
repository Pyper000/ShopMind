import { readFile, writeFile } from 'node:fs/promises';

const catalogPath = new URL('../api/catalog.json', import.meta.url);
const taxonomyPath = new URL('../api/category-taxonomy.json', import.meta.url);
const catalog = JSON.parse(await readFile(catalogPath, 'utf8'));
const taxonomy = JSON.parse(await readFile(taxonomyPath, 'utf8'));
const existingIds = new Set(catalog.map(product => product.id));
const styles = [
  { label: '基础实用款', price: 39, note: '基础功能定位，适合先满足日常使用。' },
  { label: '轻量便携款', price: 69, note: '以轻便和易收纳为定位，适合空间或携带受限时比较。' },
  { label: '耐用升级款', price: 129, note: '以耐用和使用便利为定位，具体材质与规格待核实。' },
  { label: '功能进阶款', price: 199, note: '以功能配置更完整为定位，具体功能参数待核实。' },
  { label: '品质进阶款', price: 299, note: '以做工和体验升级为定位，具体性能与售后待核实。' },
];
let nextId = Math.max(0, ...catalog.map(product => Number(product.id.replace(/^P/, '')) || 0)) + 1;
let added = 0;

for (const [category, subcategories] of Object.entries(taxonomy)) {
  for (const [subcategory, ids] of Object.entries(subcategories)) {
    while (ids.length < 5) {
      const style = styles[ids.length];
      let id;
      do { id = `P${nextId++}`; } while (existingIds.has(id));
      existingIds.add(id);
      catalog.push({
        id,
        title: `ShopMind模拟 ${subcategory} ${style.label}`,
        brand: 'ShopMind模拟目录',
        category,
        description: `${subcategory}类目演示候选。${style.note}本条为模拟商品与模拟价格，不代表真实在售商品；下单前需核对规格、材质、适配性、库存及售后。`,
        highlights: [
          `模拟定位: ${style.label}`,
          '真实商品参数、评价、库存与平台价格: 未接入，需核实',
        ],
        ships_to: ['CN'],
        price: style.price,
        currency: 'CNY',
        stock: 50,
      });
      ids.push(id);
      added += 1;
    }
  }
}

await writeFile(catalogPath, `${JSON.stringify(catalog, null, 2)}\n`, 'utf8');
await writeFile(taxonomyPath, `${JSON.stringify(taxonomy, null, 2)}\n`, 'utf8');
console.log(`Added ${added} simulated products; catalog now has ${catalog.length} products across ${Object.values(taxonomy).reduce((sum, groups) => sum + Object.keys(groups).length, 0)} subcategories.`);
