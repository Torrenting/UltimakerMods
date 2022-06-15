router.get('/printers/syncWeight',  function (req, res, next) {
  if(!req.query.printer || !req.query.prog) {
    let err = new Error();
    err.message = "Error: no UUID provided"
    return next(err)
  } else {
    let query = "query = '{boards(ids:[xxxxxxxxxx]) { name items { name column_values{text} } } }'" // redacted board ID for privacy
    let body = {'query': query}
    fetch("https://api.monday.com/v2", {
      method: 'POST',
      body: body,
      headers: {"Authorization": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"} // redacted API key
    }).then((res) => {
      status = res.status;
      return res.json()
    }).then((jsonResponse) => {
          let listItems = jsonResponse["data"]["boards"][0]["items"];
          for(let i = 0; i < listItems.length; i++) {
            if(listItems[i]["column_values"][1]["text"].toLowerCase() === "Paid/Printing" && listItems[i]["column_values"][4]["text"].toLowerCase() === req.query.printer.toString().toLowerCase()) {
              let weight = parseFloat(listItems[i]["column_values"][8]["text"]);
              let instantWeight = weight * parseFloat(req.query.prog);
              subtractFromMonday(req.query.printer.toString(), instantWeight).then(result => {
                res.status(200).json({
                  "result": "success"
                }).send();
              }).catch(err => {
                res.status(400).json({
                  "result": "error",
                  "error": err
                }).send();
              })
            }
          }
    }).catch(err => {
      res.status(400).json({
        "result": "error",
        "error": err
      }).send();
    })
    return next();
  }
})
