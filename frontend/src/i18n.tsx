import { ReactNode, createContext, useContext, useState } from "react";

// Packer Scan first (the screen floor workers actually use), extended to
// the Wall Builder catalog/barcode-printing screen. Squishy type names
// themselves are never translated -- they come through as entered and get
// interpolated into these strings verbatim via {params}.
export type Lang = "en" | "zh" | "es";

export const LANGUAGES: { code: Lang; label: string }[] = [
  { code: "en", label: "EN" },
  { code: "zh", label: "中文" },
  { code: "es", label: "ES" },
];

const STORAGE_KEY = "squishy_wms_lang";

type Dict = Record<string, string>;

const translations: Record<Lang, Dict> = {
  en: {
    "packerScan.wallSetLabel": "Wall set",
    "packerScan.wallSetPlaceholder": "Select a wall set...",
    "packerScan.uploaded": "uploaded",
    "packerScan.scanLabel": "Scan item",
    "packerScan.bin": "Bin {n}",
    "packerScan.goesInBin": "Goes in bin {n}.",
    "packerScan.stillNeeds": "Still needs: {items}",
    "packerScan.unknownBarcode": "Unknown barcode -- not in the catalog.",
    "packerScan.noShipmentNeedsIt": "No open shipment needs this item right now.",
    "packerScan.shipmentComplete": "Shipment complete! Label downloading...",
    "packerScan.binComplete": "Bin {n} complete: {items}. Label downloading...",

    "wallBuilder.catalogHeading": "Squishy type catalog",
    "wallBuilder.displayNameLabel": "Display name",
    "wallBuilder.displayNamePlaceholder": "e.g. Yellow Butter",
    "wallBuilder.addType": "Add type",
    "wallBuilder.catalogCount": "{count} squishy type(s) in the catalog.",
    "wallBuilder.tableName": "Name",
    "wallBuilder.tableQuantity": "Quantity",
    "wallBuilder.printBarcode": "Print barcode",
    "wallBuilder.printing": "Printing...",
    "wallBuilder.uploadHeading": "Upload orders",
    "wallBuilder.csvLabel": "TikTok CSV export",
    "wallBuilder.pdfLabel": "Shipping label / packing slip PDF",
    "wallBuilder.uploadButton": "Upload and ingest",
    "wallBuilder.uploading": "Uploading...",
    "wallBuilder.createdWallSet": "Created wall set #{id}.",
    "wallBuilder.shipmentsCreated": "{count} shipments",
    "wallBuilder.requirementsCreated": "{count} requirement lines",
    "wallBuilder.labelsMatched": "{count} label pages matched",
    "wallBuilder.unmatchedCount": "{count} product name(s) not in the catalog",
    "wallBuilder.allMatched": "Every product name matched the catalog.",
    "wallBuilder.addUnmatchedHint": "Add these as squishy types above, then re-upload before packing starts.",
    "wallBuilder.deactivate": "Deactivate",
    "wallBuilder.deactivating": "Deactivating...",
    "wallBuilder.reactivate": "Reactivate",
    "wallBuilder.reactivating": "Reactivating...",
    "wallBuilder.reactivateThisType": "Reactivate this type",
    "wallBuilder.showDeactivated": "Show deactivated types",
    "wallBuilder.showActive": "Show active types",
    "wallBuilder.noDeactivatedTypes": "No deactivated types.",
    "wallBuilder.catalogSearchLabel": "Find a type",
    "wallBuilder.catalogSearchPlaceholder": "Search by name...",
    "wallBuilder.catalogNoMatches": "No types match “{query}”.",
    "wallBuilder.wallSetNameLabel": "Wall set name",
    "wallBuilder.rename": "Rename",
    "wallBuilder.saveName": "Save",
    "wallBuilder.cancel": "Cancel",
    "wallBuilder.savingName": "Saving...",
    "wallBuilder.breakdownHeading": "Types needed this stream",
    "wallBuilder.breakdownQuantity": "Total needed",
    "wallBuilder.dropHint": "Drop a {ext} file here, or",
    "wallBuilder.dropRejectCsv": "That isn't a .csv file. The TikTok order export must be a .csv.",
    "wallBuilder.dropRejectPdf": "That isn't a .pdf file. The shipping label / packing slip must be a .pdf.",
  },
  zh: {
    "packerScan.wallSetLabel": "货墙批次",
    "packerScan.wallSetPlaceholder": "选择一个货墙批次...",
    "packerScan.uploaded": "已上传",
    "packerScan.scanLabel": "扫描商品",
    "packerScan.bin": "{n} 号格",
    "packerScan.goesInBin": "放入 {n} 号格。",
    "packerScan.stillNeeds": "仍需：{items}",
    "packerScan.unknownBarcode": "未知条码——不在目录中。",
    "packerScan.noShipmentNeedsIt": "目前没有订单需要这个商品。",
    "packerScan.shipmentComplete": "包裹已完成！正在下载标签...",
    "packerScan.binComplete": "{n} 号格已完成：{items}。正在下载标签...",

    "wallBuilder.catalogHeading": "毛绒玩具种类目录",
    "wallBuilder.displayNameLabel": "显示名称",
    "wallBuilder.displayNamePlaceholder": "例如：Yellow Butter",
    "wallBuilder.addType": "添加种类",
    "wallBuilder.catalogCount": "目录中共有 {count} 个种类。",
    "wallBuilder.tableName": "名称",
    "wallBuilder.tableQuantity": "数量",
    "wallBuilder.printBarcode": "打印条码",
    "wallBuilder.printing": "打印中...",
    "wallBuilder.uploadHeading": "上传订单",
    "wallBuilder.csvLabel": "TikTok CSV 导出文件",
    "wallBuilder.pdfLabel": "运单/装箱单 PDF",
    "wallBuilder.uploadButton": "上传并导入",
    "wallBuilder.uploading": "上传中...",
    "wallBuilder.createdWallSet": "已创建货墙批次 #{id}。",
    "wallBuilder.shipmentsCreated": "{count} 个包裹",
    "wallBuilder.requirementsCreated": "{count} 条需求记录",
    "wallBuilder.labelsMatched": "{count} 个标签页已匹配",
    "wallBuilder.unmatchedCount": "{count} 个商品名称不在目录中",
    "wallBuilder.allMatched": "所有商品名称均已匹配目录。",
    "wallBuilder.addUnmatchedHint": "请先在上方添加这些种类，然后重新上传再开始打包。",
    "wallBuilder.deactivate": "停用",
    "wallBuilder.deactivating": "正在停用...",
    "wallBuilder.reactivate": "重新启用",
    "wallBuilder.reactivating": "正在重新启用...",
    "wallBuilder.reactivateThisType": "重新启用此种类",
    "wallBuilder.showDeactivated": "显示已停用的种类",
    "wallBuilder.showActive": "显示启用中的种类",
    "wallBuilder.noDeactivatedTypes": "没有已停用的种类。",
    "wallBuilder.catalogSearchLabel": "查找种类",
    "wallBuilder.catalogSearchPlaceholder": "按名称搜索...",
    "wallBuilder.catalogNoMatches": "没有与“{query}”匹配的种类。",
    "wallBuilder.wallSetNameLabel": "货墙批次名称",
    "wallBuilder.rename": "重命名",
    "wallBuilder.saveName": "保存",
    "wallBuilder.cancel": "取消",
    "wallBuilder.savingName": "保存中...",
    "wallBuilder.breakdownHeading": "本次直播所需种类",
    "wallBuilder.breakdownQuantity": "所需总数",
    "wallBuilder.dropHint": "将 {ext} 文件拖放到此处，或",
    "wallBuilder.dropRejectCsv": "这不是 .csv 文件。TikTok 订单导出必须是 .csv。",
    "wallBuilder.dropRejectPdf": "这不是 .pdf 文件。运单/装箱单必须是 .pdf。",
  },
  es: {
    "packerScan.wallSetLabel": "Conjunto de pared",
    "packerScan.wallSetPlaceholder": "Selecciona un conjunto de pared...",
    "packerScan.uploaded": "subido",
    "packerScan.scanLabel": "Escanear artículo",
    "packerScan.bin": "Contenedor {n}",
    "packerScan.goesInBin": "Va en el contenedor {n}.",
    "packerScan.stillNeeds": "Aún necesita: {items}",
    "packerScan.unknownBarcode": "Código desconocido -- no está en el catálogo.",
    "packerScan.noShipmentNeedsIt": "Ningún pedido abierto necesita este artículo ahora mismo.",
    "packerScan.shipmentComplete": "¡Pedido completo! Descargando etiqueta...",
    "packerScan.binComplete": "Contenedor {n} completo: {items}. Descargando etiqueta...",

    "wallBuilder.catalogHeading": "Catálogo de tipos de squishy",
    "wallBuilder.displayNameLabel": "Nombre para mostrar",
    "wallBuilder.displayNamePlaceholder": "p. ej. Yellow Butter",
    "wallBuilder.addType": "Agregar tipo",
    "wallBuilder.catalogCount": "{count} tipo(s) de squishy en el catálogo.",
    "wallBuilder.tableName": "Nombre",
    "wallBuilder.tableQuantity": "Cantidad",
    "wallBuilder.printBarcode": "Imprimir código de barras",
    "wallBuilder.printing": "Imprimiendo...",
    "wallBuilder.uploadHeading": "Subir pedidos",
    "wallBuilder.csvLabel": "Exportación CSV de TikTok",
    "wallBuilder.pdfLabel": "PDF de etiqueta de envío / lista de empaque",
    "wallBuilder.uploadButton": "Subir e importar",
    "wallBuilder.uploading": "Subiendo...",
    "wallBuilder.createdWallSet": "Conjunto de pared #{id} creado.",
    "wallBuilder.shipmentsCreated": "{count} pedidos",
    "wallBuilder.requirementsCreated": "{count} líneas de requisitos",
    "wallBuilder.labelsMatched": "{count} páginas de etiqueta coincidieron",
    "wallBuilder.unmatchedCount": "{count} nombre(s) de producto no están en el catálogo",
    "wallBuilder.allMatched": "Todos los nombres de producto coinciden con el catálogo.",
    "wallBuilder.addUnmatchedHint": "Agrega estos como tipos de squishy arriba y vuelve a subir antes de empezar a empacar.",
    "wallBuilder.deactivate": "Desactivar",
    "wallBuilder.deactivating": "Desactivando...",
    "wallBuilder.reactivate": "Reactivar",
    "wallBuilder.reactivating": "Reactivando...",
    "wallBuilder.reactivateThisType": "Reactivar este tipo",
    "wallBuilder.showDeactivated": "Mostrar tipos desactivados",
    "wallBuilder.showActive": "Mostrar tipos activos",
    "wallBuilder.noDeactivatedTypes": "No hay tipos desactivados.",
    "wallBuilder.catalogSearchLabel": "Buscar un tipo",
    "wallBuilder.catalogSearchPlaceholder": "Buscar por nombre...",
    "wallBuilder.catalogNoMatches": "Ningún tipo coincide con “{query}”.",
    "wallBuilder.wallSetNameLabel": "Nombre del conjunto de pared",
    "wallBuilder.rename": "Renombrar",
    "wallBuilder.saveName": "Guardar",
    "wallBuilder.cancel": "Cancelar",
    "wallBuilder.savingName": "Guardando...",
    "wallBuilder.breakdownHeading": "Tipos necesarios este directo",
    "wallBuilder.breakdownQuantity": "Total necesario",
    "wallBuilder.dropHint": "Suelta un archivo {ext} aquí, o",
    "wallBuilder.dropRejectCsv": "Eso no es un archivo .csv. La exportación de pedidos de TikTok debe ser .csv.",
    "wallBuilder.dropRejectPdf": "Eso no es un archivo .pdf. La etiqueta de envío / lista de empaque debe ser .pdf.",
  },
};

function readStoredLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "zh" || stored === "es") return stored;
  } catch {
    // localStorage unavailable (private browsing, etc.) -- fall through to default
  }
  return "en";
}

interface LanguageContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const LanguageContext = createContext<LanguageContextValue | null>(null);

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(readStoredLang);

  function setLang(next: Lang) {
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // ignore -- the choice just won't persist across reloads
    }
  }

  function t(key: string, params?: Record<string, string | number>): string {
    let text = translations[lang][key] ?? translations.en[key] ?? key;
    if (params) {
      for (const [param, value] of Object.entries(params)) {
        text = text.replace(`{${param}}`, String(value));
      }
    }
    return text;
  }

  return <LanguageContext.Provider value={{ lang, setLang, t }}>{children}</LanguageContext.Provider>;
}

export function useTranslation(): LanguageContextValue {
  const ctx = useContext(LanguageContext);
  if (!ctx) throw new Error("useTranslation must be used within a LanguageProvider");
  return ctx;
}

export function LanguageSwitcher() {
  const { lang, setLang } = useTranslation();
  return (
    <div className="lang-switcher">
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          type="button"
          className={lang === l.code ? "lang-active" : "secondary"}
          onClick={() => setLang(l.code)}
        >
          {l.label}
        </button>
      ))}
    </div>
  );
}
