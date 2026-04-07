name: <tool_name>
description: <这个接口是干嘛的（给 AI 看）>

input_schema:
  type: object
  properties:
    <参数名>:
      type: <类型>
      description: <参数说明>
  required: [必须参数]

output_schema:
  type: object
  properties:
    <返回字段>:
      type: <类型>
      description: <字段含义>


invoke:
  method: GET | POST
  url: /api/xxx
  headers: {}
  query: {}
  body: {}