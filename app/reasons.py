REASONS = {
    'EMPTY_FILE': '文件为空', 'BLANK_FILE': '文件只有空白行', 'HEADER_ONLY': '文件只有表头',
    'BAD_ENCODING': '文件须为 UTF-8 或 GB18030', 'BAD_HEADER': '文件列名与模板不符',
    'BAD_CSV': 'CSV 引号未闭合或格式错误', 'TOO_MANY_ROWS': '文件超过 2000 行',
    'REPEATED_HEADER': '分段重现表头', 'BLANK_ROW': '空行', 'COLUMN_COUNT': '列数不匹配',
    'MISSING_FIELD': '必填字段缺失', 'UNKNOWN_STATION': '站点编号不存在',
    'BAD_TIME': '时间缺少偏移或格式无效', 'BAD_KIND': '事件类型仅限借车或还车',
    'OUTSIDE_SHIFT': '超出班次时间', 'RECEIVED_EARLY': '收件早于实际发生',
    'DUPLICATE_EVENT': '同一尝试重复上报', 'EVENT_CONFLICT': '同一编号对应不同尝试',
    'BAD_CAPACITY': '容量或修订号无效', 'CAPACITY_INTERVAL': '容量结束时点须晚于起点',
    'CAPACITY_CONFLICT': '同站同修订的容量区间重叠', 'CAPACITY_GAP': '班次内缺少容量版本',
    'STATION_CLOSED': '站点关闭', 'NO_BIKE': '无可借车辆', 'NO_DOCK': '无空桩',
    'NO_ROAD_BIKE': '在骑池无可归还车辆', 'BAD_PLAN': '调拨计划格式错误',
    'UNKNOWN_TRUCK': '车辆编号不存在', 'TRUCK_LOAD': '装车数量超过载重',
    'TRUCK_OVERLAP': '车辆时间重叠或未出班', 'TRUCK_POSITION': '车辆不在装车站',
    'TRAVEL_TIME': '行程短于最小通行时间', 'SAME_STATION': '装卸站须不同',
    'PICKUP_SHORT': '出发站可调车辆不足', 'DROP_FULL': '到达时卸车站空桩不足',
    'STALE_RUN': '班次已更新，请加载当前版本', 'RUN_NOT_FOUND': '未找到班次记录',
    'INVENTORY_MISMATCH': '库存勾稽失败', 'UPLOAD_REQUIRED': '请选择事件文件',
    'NO_ACCEPTED_EVENTS': '无可采信事件，请修正文件', 'BODY_TOO_LARGE': '文件体积超限',
    'BAD_REQUEST': '请求格式无效', 'REPLAY_DRIFT': '重跑结果与历史记录不一致',
    'STORE_UNAVAILABLE': '班次存储暂不可用，请重试',
    'UNKNOWN_BIKE_STATION': '账面车辆的归属站点不存在', 'BIKE_BOOK_GAP': '账面车辆册与班次期末勾稽不一致',
    'COUNT_DUPLICATE_BIKE': '同一车辆在点数单内重复出现', 'COUNT_TIME_INVALID': '点数时点缺失或格式无效',
    'COUNT_OUTSIDE_WINDOW': '点数时点不在本班次窗口内', 'COUNT_SHEET_NOT_FOUND': '未找到盘点记录',
    'CLOSING_NOT_FOUND': '未找到关账结论', 'NO_CLOSING_INPUT': '请先上传现场点数单',
    'STALE_CLOSING': '关账结论已更新，请加载当前版本后重算', 'CLOSING_SUPERSEDED': '该结论已随方案发布失效，需重算',
}

DISPOSITIONS = {
    'MATCHED': '盘平 · 账面与现场一致',
    'STAGED_CLOSED': '关站暂存 · 有出处，计入开班基数',
    'CONFIRMED_TRANSFER': '冲突采信 · 有出处，计入开班基数',
    'CONFLICT_REJECT': '冲突败方 · 隔离不调账',
    'SHORTAGE_SUSPENSE': '盘亏挂起 · 无出处，不计入基数',
    'SURPLUS_SUSPENSE': '盘盈挂起 · 无出处，不计入基数',
}

# 计入下一班开班基数调整的处置类型（每辆都有可追溯出处）
BASE_ADJUSTED = {'STAGED_CLOSED', 'CONFIRMED_TRANSFER'}
SUSPENDED = {'SHORTAGE_SUSPENSE', 'SURPLUS_SUSPENSE'}
