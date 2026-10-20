// Business-flow smoke tests inject host controls rather than bundle their CSS/portals.
// Keep native form events and the Lako controlled-value contract in this adapter.
const React = require("react");
const field = (tag) => ({ label, hint, error, containerClassName, prefix, suffix, ...props }) => React.createElement("label", { className: containerClassName }, label && React.createElement("span", null, label), prefix, React.createElement(tag, props), suffix, hint, error);
module.exports = {
  reducedMotion: () => true,
  useAnimatedTabs: ({ initial, onCommit }) => {
    const [active, set] = React.useState(initial);
    return { active, committed: active, phase: "", setActive(next) { set(next); onCommit?.(next); }, jumpTo(next) { set(next); onCommit?.(next); } };
  },
  useDialogExit: (onClosed) => ({ visible: true, close: onClosed }),
  LakoButton: ({ children, variant, size, loading, leadingIcon, trailingIcon, ...props }) => React.createElement("button", { type: "button", ...props, disabled: props.disabled || loading }, children),
  LakoInputBox: field("input"),
  LakoTextarea: field("textarea"),
  LakoTabs: ({ items, value, onChange, ariaLabel }) => React.createElement("div", { role: "tablist", "aria-label": ariaLabel }, items.map((item) => React.createElement("button", { key: item.id, role: "tab", "aria-selected": value === item.id, onClick: () => onChange(item.id) }, item.label))),
  LakoDialog: ({ open, title, children, actions }) => open ? React.createElement("div", { role: "dialog" }, React.createElement("h2", null, title), children, actions) : null,
  FileSelect: ({ children, value, onValueChange, ariaLabel, ...props }) => React.createElement("select", { ...props, value, "aria-label": ariaLabel ?? props["aria-label"], onChange: (event) => onValueChange(event.target.value) }, children),
  FilterableSearch: ({ value, onChange, onSubmit, placeholder, searchLabel, children }) => React.createElement("div", null, React.createElement("form", { onSubmit }, React.createElement("input", { value, placeholder, onChange: (event) => onChange(event.target.value) }), React.createElement("button", { type: "submit" }, searchLabel)), children),
};
