(function () {
  var reason = new URLSearchParams(location.search).get("error");
  var messages = {
    wrong: "That username and password don't match. Try again.",
    locked: "Too many failed attempts. Wait 15 minutes, then try again.",
  };
  if (messages[reason]) {
    var el = document.getElementById("err");
    el.textContent = messages[reason];
    el.hidden = false;
  }
})();
