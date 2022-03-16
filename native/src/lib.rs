#![feature(once_cell)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use select::document::Document;
use select::node::{Data, Node};
use select::predicate::{Attr, Class, Name, Predicate};

use std::collections::HashMap;
use std::lazy::SyncOnceCell;

static mut DOCUMENT_STORE: SyncOnceCell<HashMap<String, Document>> = SyncOnceCell::new();

/// This function exists as requests to HTML are done in Python.
///
/// We must natively check if the document has already been parsed, and if so,
/// don't make a request.
#[pyfunction]
fn has_document(url: &str) -> bool {
    unsafe {
        if let Some(store) = DOCUMENT_STORE.get() {
            store.contains_key(url)
        } else {
            false
        }
    }
}

enum WrappedNode<'n> {
    Element(Node<'n>),
    Text(String),
}

fn walk_nodes(node: Node) -> Vec<WrappedNode> {
    let mut result: Vec<WrappedNode> = Vec::new();

    for child in node.children() {
        match child.data() {
            Data::Text(text) => {
                if text.len() > 0 {
                    result.push(WrappedNode::Text(text.to_string()));
                }
            }
            Data::Element(ref name, _) => {
                let unwrapped: &str = &name.local;

                if ["p", "a", "b", "i", "em", "strong", "u", "ul", "ol", "code"]
                    .iter()
                    .any(|tag| *tag == unwrapped)
                // contains requires a static string
                {
                    result.push(WrappedNode::Element(child));
                    continue;
                }

                if unwrapped.starts_with("h") {
                    result.push(WrappedNode::Element(child));
                    continue;
                }

                let class_list = if let Some(classes) = child.attr("class") {
                    classes.split(" ").collect::<Vec<_>>()
                } else {
                    continue;
                };

                if unwrapped == "dl" {
                    if !class_list.contains(&"field-list") {
                        break;
                    }

                    result.push(WrappedNode::Element(child));
                    continue;
                }

                if unwrapped == "div" {
                    if [
                        "admonition",
                        "operations",
                        "highlight-python3",
                        "highlight-default",
                    ]
                    .iter()
                    .any(|tag| class_list.contains(tag))
                    {
                        result.push(WrappedNode::Element(child));
                        continue;
                    }
                }

                result.extend(walk_nodes(child));
            }
            _ => {}
        }
    }

    result
}

#[pyclass]
#[derive(Clone)]
struct EmbedField {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    value: String,
    #[pyo3(get)]
    inline: bool,
}

impl EmbedField {
    fn new(name: String, value: String) -> Self {
        Self {
            name,
            value,
            inline: false, // embed fields are always inline
        }
    }
}

fn parse_node(node: Node, url: &str) -> (String, Vec<EmbedField>) {
    if matches!(node.data(), Data::Text(_)) {
        return (node.text(), vec![]);
    }

    let mut result = String::new();
    let mut pending_rubric: Option<String> = None;
    let mut fields: Vec<EmbedField> = Vec::new();

    let mut _recur = |element, fields: &mut Vec<EmbedField>| {
        let (r, f) = parse_node(element, url);
        fields.extend(f);

        r
    };

    for child in walk_nodes(node) {
        match child {
            WrappedNode::Text(text) => {
                result.push_str(&text);
            }
            WrappedNode::Element(element) => {
                let class_list = element
                    .attr("class")
                    .unwrap_or("")
                    .split(" ")
                    .collect::<Vec<_>>();

                match element.name().unwrap() {
                    "p" => {
                        if class_list.contains(&"rubric") {
                            pending_rubric = Some(element.text().trim().to_string());
                            continue;
                        }
                        result.push_str(_recur(element, &mut fields).as_str());
                    }
                    "a" => {
                        let inner = _recur(element, &mut fields);
                        let mut href = match element.attr("href") {
                            Some(href) => href.to_string(),
                            None => continue, // what's a tag without an href?
                        };

                        if !href.contains("://") {
                            href = url.to_string() + &href;
                        }

                        result.push_str(&format!("[{}]({})", inner, href));
                    }
                    "b" | "strong" => {
                        result.push_str(&format!("**{}**", _recur(element, &mut fields)));
                    }
                    "i" | "em" => {
                        result.push_str(&format!("*{}*", _recur(element, &mut fields)));
                    }
                    "u" => {
                        result.push_str(&format!("__{}__", _recur(element, &mut fields)));
                    }
                    "code" => {
                        result.push_str(&format!("`{}`", _recur(element, &mut fields)));
                    }
                    "ul" => {
                        result.push('\n');

                        element.find(Name("li")).for_each(|li| {
                            result.push_str(&format!("\u{2022} {}\n", _recur(li, &mut fields)));
                        });
                    }
                    "ol" => {
                        element.find(Name("li")).enumerate().for_each(|(i, li)| {
                            result.push_str(&format!("{}. {}\n", i, _recur(li, &mut fields)));
                        });
                    }
                    "div" => {
                        if class_list.contains(&"admonition") {
                            let first = match element.find(Name("p")).next() {
                                Some(p) => p,
                                None => continue,
                            };

                            let title = _recur(first, &mut fields).trim().to_string();
                            let content = _recur(
                                match first.next() {
                                    Some(p) => p,
                                    None => continue,
                                },
                                &mut fields,
                            )
                            .trim()
                            .to_string();

                            if title.chars().count() <= 0 || content.chars().count() <= 0 {
                                continue;
                            }

                            fields.push(EmbedField::new(title, content));
                        } else if pending_rubric.is_some()
                            && class_list.contains(&"highlight-python3")
                        {
                            fields.push(EmbedField::new(
                                pending_rubric.take().unwrap(),
                                format!("```py\n{}```", element.text()),
                            ));
                        } else if class_list.contains(&"highlight-default") {
                            result.push_str(&format!("```\n{}```", element.text()));
                        }

                        let mut chunks: Vec<String> = Vec::new();

                        for child in element.find(Name("dl").and(Class("describe"))) {
                            let operation =
                                _recur(child.find(Name("dt")).next().unwrap(), &mut fields);

                            let description =
                                _recur(child.find(Name("dd")).next().unwrap(), &mut fields);

                            chunks.push(format!(
                                "**`{}`** - {}",
                                operation.trim(),
                                description.replace("\n", " ").trim(),
                            ));
                        }

                        if chunks.len() > 0 {
                            fields.push(EmbedField::new(
                                "Supported Operations".to_string(),
                                chunks.join("\n"),
                            ));
                        }
                    }
                    "dl" => {
                        for (dt, dd) in element.find(Name("dt")).zip(element.find(Name("dd"))) {
                            let dt = _recur(dt, &mut fields);
                            let dd = _recur(dd, &mut fields);

                            if dt.chars().count() > 0 && dd.chars().count() > 0 {
                                fields.push(EmbedField::new(dt, dd));
                            } else if dd.chars().count() <= 0 {
                                fields
                                    .push(EmbedField::new(dt, "No content provided.".to_string()));
                            }
                        }
                    }
                    _ => {}
                }
            }
        }
    }

    (result, fields)
}

#[pyclass]
#[derive(Clone)]
struct AnsiStringSection {
    #[pyo3(get)]
    content: String,
    #[pyo3(get)]
    bold: bool,
    #[pyo3(get)]
    color: String, // probably just call getattr() with this being the argument
}

fn parse_signature_node(node: Node) -> Vec<AnsiStringSection> {
    let mut sections: Vec<AnsiStringSection> = Vec::new();

    for child in node.children() {
        match child.data() {
            Data::Text(_) => {
                sections.push(AnsiStringSection {
                    content: child.text().to_string(),
                    bold: true,
                    color: "gray".to_string(),
                });
            }
            Data::Element(ref qualname, _) => {
                let name: &str = &qualname.local;
                let classes = child
                    .attr("class")
                    .unwrap_or("")
                    .split(" ")
                    .collect::<Vec<&str>>();

                if name == "em" && !classes.contains(&"sig-param") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: false,
                        color: "green".to_string(),
                    });
                } else if classes.contains(&"sig-paren") || classes.contains(&"o") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: true,
                        color: "gray".to_string(),
                    });
                } else if classes.contains(&"n") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: false,
                        color: "yellow".to_string(),
                    });
                } else if classes.contains(&"default_value") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: false,
                        color: "cyan".to_string(),
                    });
                } else if classes.contains(&"sig-prename") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: false,
                        color: if classes.contains(&"descclassname") {
                            "white".to_string()
                        } else {
                            "red".to_string()
                        },
                    });
                } else if classes.contains(&"descname") || classes.contains(&"sig-name") {
                    sections.push(AnsiStringSection {
                        content: child.text().to_string(),
                        bold: true,
                        color: "white".to_string(),
                    });
                } else if classes.contains(&"sig-param") {
                    sections.extend(parse_signature_node(child));
                }
            }
            _ => {}
        }
    }

    sections
}

#[pyclass]
struct SphinxDocumentResult {
    #[pyo3(get)]
    description: String,
    #[pyo3(get)]
    signature: Vec<AnsiStringSection>,
    #[pyo3(get)]
    fields: Vec<EmbedField>,
}

#[pyfunction]
fn scrape_document(url: &str, html: &str, target: &str) -> PyResult<SphinxDocumentResult> {
    let document = unsafe {
        let store = if let Some(store) = DOCUMENT_STORE.get_mut() {
            store
        } else {
            DOCUMENT_STORE.set(HashMap::new()).unwrap();

            DOCUMENT_STORE.get_mut().unwrap()
        };

        if let Some(document) = store.get(url) {
            document
        } else {
            let document = Document::from(html);
            store.insert(url.to_string(), document);

            store.get(url).unwrap()  // returns the exact same document without moving it.
        }
    };

    let predicate = Name("dt").and(Attr("id", target));
    let signature = document
        .find(predicate)
        .next()
        .ok_or(PyErr::new::<PyValueError, _>("Could not find sig tag"))?;

    let parent = signature.parent().unwrap();
    let (description, fields) = {
        let node = parent
            .find(Name("dd"))
            .next()
            .ok_or(PyErr::new::<PyValueError, _>("Could not find dd tag"))?;

        parse_node(node, url)
    };
    let ansi_sections = parse_signature_node(signature);

    Ok(SphinxDocumentResult {
        description,
        signature: ansi_sections,
        fields,
    })
}

#[pyfunction]
fn hello_world() -> PyResult<String> {
    Ok(String::from("Hello, world!"))
}

#[pymodule]
fn app_native(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_wrapped(wrap_pyfunction!(hello_world))?;
    m.add_wrapped(wrap_pyfunction!(has_document))?;
    m.add_wrapped(wrap_pyfunction!(scrape_document))?;
    m.add_class::<EmbedField>()?;
    m.add_class::<AnsiStringSection>()?;
    m.add_class::<SphinxDocumentResult>()?;

    Ok(())
}
