ALTER VIEW dbo.vw_xmk_project_contract_report (
    [项目ID],
    [人名],
    [工号],
    [所属部门],
    [项目名称],
    [项目类型],
    [项目负责人],
    [项目联系人],
    [项目合同金额],
    [项目合同名称],
    [合同乙方单位],
    [项目经费来源],
    [项目简介],
    [经费类型],
    [项目入库时间],
    [合同已付金额],
    [中标单位],
    [项目预算],
    [项目阶段],
    [项目状态],
    [申报类别],
    [经费类型年份],
    [项目网格员],
    [项目启动日期],
    [项目中标时间],
    [经费类型编码],
    [经费年份]
) AS
SELECT
    x.id,
    xmlxr.lastname,
    xmlxr.workcode,
    xmlxr_dept.departmentname,
    x.xmmc,
    si_xmlx.selectname,
    xmfzr.lastname,
    xmlxr.lastname,
    x.htzje,
    NULLIF(x.htmc, ''),
    zbdw.gysmc,
    si_xmjfly.selectname,
    CAST(x.xmjj AS varchar(max)),
    si_jflx.selectname,
    NULLIF(x.xmrksj, ''),
    x.yfkje,
    zbdw.gysmc,
    x.xmzyswy,
    si_xmjd.selectname,
    si_xmzt.selectname,
    si_sblb.selectname,
    x.jflxnf,
    COALESCE(wgy_hr.lastname, xmwgy_hr.lastname),
    NULLIF(x.xmqdr, ''),
    NULLIF(x.zbtzsrq, ''),
    x.jflx,
    si_jflxnf.selectname
FROM dbo.uf_xmk AS x
LEFT JOIN dbo.HrmResource AS xmfzr
    ON x.xmfzrxm = xmfzr.id
LEFT JOIN dbo.HrmResource AS xmlxr
    ON x.xmlxrxm = xmlxr.id
LEFT JOIN dbo.HrmDepartment AS xmlxr_dept
    ON xmlxr.departmentid = xmlxr_dept.id
LEFT JOIN dbo.uf_gysk AS zbdw
    ON x.zbdw = CAST(zbdw.id AS varchar(1000))
LEFT JOIN dbo.uf_wgygl AS wgy
    ON NULLIF(LTRIM(RTRIM(x.szwanggeyuan)), '') = CAST(wgy.id AS varchar(1000))
LEFT JOIN dbo.HrmResource AS wgy_hr
    ON wgy.xm = wgy_hr.id
LEFT JOIN dbo.HrmResource AS xmwgy_hr
    ON x.xmwgyrlzy = xmwgy_hr.id
LEFT JOIN dbo.workflow_selectitem AS si_xmlx
    ON si_xmlx.isbill = 1
   AND si_xmlx.fieldid = 10254
   AND si_xmlx.selectvalue = x.xmlx
LEFT JOIN dbo.workflow_selectitem AS si_xmjfly
    ON si_xmjfly.isbill = 1
   AND si_xmjfly.fieldid = 10266
   AND si_xmjfly.selectvalue = x.xmjfly
LEFT JOIN dbo.workflow_selectitem AS si_jflx
    ON si_jflx.isbill = 1
   AND si_jflx.fieldid = 10657
   AND si_jflx.selectvalue = x.jflx
LEFT JOIN dbo.workflow_selectitem AS si_xmjd
    ON si_xmjd.isbill = 1
   AND si_xmjd.fieldid = 11594
   AND si_xmjd.selectvalue = x.xmjd
LEFT JOIN dbo.workflow_selectitem AS si_xmzt
    ON si_xmzt.isbill = 1
   AND si_xmzt.fieldid = 10285
   AND si_xmzt.selectvalue = x.xmzt
LEFT JOIN dbo.workflow_selectitem AS si_sblb
    ON si_sblb.isbill = 1
   AND si_sblb.fieldid = 11611
   AND si_sblb.selectvalue = x.sblb
LEFT JOIN dbo.workflow_selectitem AS si_jflxnf
    ON si_jflxnf.isbill = 1
   AND si_jflxnf.fieldid = 12163
   AND si_jflxnf.selectvalue = x.jflxnf;
