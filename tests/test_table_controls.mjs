import test from 'node:test';
import assert from 'node:assert/strict';
import {matchesColumn,selectRows} from '../static/table-controls.js';
const columns=[{key:'name',value:r=>r.name},{key:'id',value:r=>r.id},{key:'score',type:'number',value:r=>r.score}];
test('筛选全部数据后分页，学号自然排序不受原始顺序影响',()=>{
 const source=Array.from({length:85},(_,i)=>({id:String(85-i),name:i%2?'甲':'乙',score:i}));
 const result=selectRows(source,columns,{sort:'id',direction:1,filters:{name:'甲'}});
 assert.equal(result.length,42);assert.equal(result.slice(40,80)[0].id,'82');assert.equal(source[0].id,'85');
});
test('分数按数值排序，空值两种顺序都放末尾',()=>{
 const rows=[{score:10},{score:null},{score:2},{score:0}];
 assert.deepEqual(selectRows(rows,columns,{sort:'score',direction:1}).map(r=>r.score),[0,2,10,null]);
 assert.deepEqual(selectRows(rows,columns,{sort:'score',direction:-1}).map(r=>r.score),[10,2,0,null]);
});
test('多列组合筛选，数字边界与零分',()=>{
 const rows=[{name:'甲',score:2},{name:'甲',score:10},{name:'乙',score:3},{name:'甲',score:null}];
 assert.deepEqual(selectRows(rows,columns,{filters:{name:'甲',score:'≤2'}}),[rows[0]]);
 assert.equal(matchesColumn(0,'=0','number'),true);assert.equal(matchesColumn(null,'=0','number'),false);
 assert.equal(matchesColumn(2,'≥2','number'),true);assert.equal(matchesColumn(2,'>2','number'),false);
 assert.equal(matchesColumn(2,'bad','number'),false);
});
test('中文文字匹配、精确匹配和默认顺序',()=>{
 assert.equal(matchesColumn('已核算 / 待审核','待审核'),true);assert.equal(matchesColumn('未核算','=已核算'),false);
 const rows=[{name:'乙'},{name:'甲'}];assert.deepEqual(selectRows(rows,columns,{}),rows);
 assert.equal(matchesColumn('AbC','abc'),true);
});
